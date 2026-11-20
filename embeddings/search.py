"""The semantic leg of a ``search`` filter, shared by every filter that offers one."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Case, F, FloatField, IntegerField, Q, QuerySet, Value, When
from pgvector.django import CosineDistance

from embeddings import engine

logger = logging.getLogger(__name__)


def query_vector(value: str) -> list[float] | None:
    """The query's vector, or ``None`` when the semantic leg cannot run (off, unloadable, zeros)."""
    if not engine.enabled():
        return None
    try:
        return engine.embed_query(value)
    except engine.EmbeddingsUnavailable as e:
        logger.warning("Embeddings unavailable (%s); search %r is lexical-only", e, value)
        return None


def hybrid_search(queryset: QuerySet[Any], prefix: str, value: str, lexical: Q, name: str) -> tuple[QuerySet[Any], Q]:
    """``lexical`` OR (embedded AND cosine distance below the threshold), ranked on ``name``.

    Four tiers: ``name`` is the text, ``name`` starts with it, any other ``lexical`` hit,
    semantic-only. Lexical hits tie-break by trigram similarity to ``name`` (the text covering
    more of a shorter name wins), then every tier by distance, then pk. Needs ``pg_trgm``.
    strawberry_django applies an explicit ``ordering`` argument after the filters, replacing
    this order -- which is what a client asking for alphabetical results wants.

    Nested (``prefix``) or blank: ``lexical`` as given, unranked. Embeddings disabled, the
    model unloadable or a query embedding to zeros: ranked, lexical-only. A missing ``vector``
    extension or column is *not* caught: that is a deployment fault and must surface.

    The aliases encode the value: ``AND``/``OR`` combinators recurse over one queryset with
    the same prefix, and Django keeps the first annotation for a repeated alias, so two
    different search strings in one query must not share a name.
    """
    needle = value.strip()
    if prefix or not needle:
        return queryset, lexical
    vector = query_vector(needle)

    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=4).hexdigest()
    rank, similarity, distance = f"_search_rank_{digest}", f"_search_similarity_{digest}", f"_search_distance_{digest}"
    if rank not in queryset.query.annotations:
        annotations: dict[str, Any] = {
            rank: Case(
                When(**{f"{name}__iexact": needle}, then=Value(0)),
                When(**{f"{name}__istartswith": needle}, then=Value(1)),
                When(lexical, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            ),
            # Only among lexical hits: a semantic-only row is ordered by its distance alone.
            similarity: Case(When(lexical, then=TrigramSimilarity(name, needle)), default=Value(0.0), output_field=FloatField()),
        }
        if vector is not None:
            annotations[distance] = CosineDistance("embedding", vector)
        queryset = queryset.annotate(**annotations)
    order: list[Any] = [rank, F(similarity).desc(nulls_last=True)]
    if vector is not None:
        order.append(F(distance).asc(nulls_last=True))
    queryset = queryset.order_by(*order, "pk")
    if vector is None:
        return queryset, lexical

    # NULL embeddings give a NULL distance, which is never below the threshold: unembedded rows
    # are excluded from the vector leg and reachable lexically only.
    semantic = Q(embedding__isnull=False) & Q(**{f"{distance}__lt": engine.distance_threshold()})
    return queryset, lexical | semantic


#: How many neighbours a "show me similar rows" read returns when the caller does not say.
DEFAULT_NEIGHBOURS = 10


def neighbourhood(
    queryset: QuerySet[Any],
    vector: Any,
    *,
    exclude_pk: Any = None,
    threshold: float | None = None,
) -> tuple[QuerySet[Any], Q]:
    """``queryset`` ordered by distance to ``vector``, with the predicate that selects it.

    The other half of the semantic story: :func:`hybrid_search` answers "what matches this
    text", this one answers "what is like this row" -- the grouping a catalogue needs when
    two teams ship the same action under two names.

    Returned as ``(queryset, Q)`` rather than as a finished queryset so a filter can compose
    it with its siblings and with pagination. Nothing is sliced here: "the ten nearest, then
    narrowed by kind" would hand back nothing whenever the matching rows sit at rank eleven.

    ``threshold`` is not applied unless asked for. ``engine.distance_threshold()`` is tuned
    for "does this row answer that query"; imposing it on a neighbourhood would produce an
    empty list for every row that happens to sit in a sparse corner of the space, which reads
    as "this action is unique" when it means "nothing is *very* close".

    Rows without a vector are excluded, as in :func:`hybrid_search`.
    """
    if vector is None or not engine.enabled():
        return queryset, Q(pk__in=[])

    if "_neighbour_distance" not in queryset.query.annotations:
        queryset = queryset.annotate(_neighbour_distance=CosineDistance("embedding", vector))
    queryset = queryset.order_by("_neighbour_distance", "pk")

    predicate = Q(embedding__isnull=False)
    if exclude_pk is not None:
        predicate &= ~Q(pk=exclude_pk)
    if threshold is not None:
        predicate &= Q(_neighbour_distance__lt=threshold)
    return queryset, predicate


def neighbours(
    queryset: QuerySet[Any],
    vector: Any,
    *,
    exclude_pk: Any = None,
    limit: int = DEFAULT_NEIGHBOURS,
    threshold: float | None = None,
) -> QuerySet[Any]:
    """The ``limit`` rows of ``queryset`` nearest to ``vector``, nearest first."""
    queryset, predicate = neighbourhood(queryset, vector, exclude_pk=exclude_pk, threshold=threshold)
    return queryset.filter(predicate)[:limit]
