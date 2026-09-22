"""The semantic leg of a ``search`` filter, shared by every filter that offers one."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.db.models import Case, F, IntegerField, Q, QuerySet, Value, When
from pgvector.django import CosineDistance

from embeddings import engine

logger = logging.getLogger(__name__)


def hybrid_search(queryset: QuerySet[Any], prefix: str, value: str, lexical: Q) -> tuple[QuerySet[Any], Q]:
    """``lexical`` OR (same embedding model AND cosine distance below the threshold), ranked.

    Ranking is two-tier: rows the lexical predicate matches first, then by distance, then by
    pk. A substring hit is a hard signal, and ranking it by its own distance would order an
    unembedded substring hit differently from an embedded one. strawberry_django applies an
    explicit ``ordering`` argument after the filters, replacing this order -- which is what a
    client asking for alphabetical results wants.

    Lexical-only when the filter is nested (``prefix``), the value is blank, embeddings are
    disabled, the model cannot be loaded, or the query embeds to zeros. A missing ``vector``
    extension or column is *not* caught: that is a deployment fault and must surface.

    The distance alias encodes the value: ``AND``/``OR`` combinators recurse over one queryset
    with the same prefix, and Django keeps the first annotation for a repeated alias, so two
    different search strings in one query must not share a name.
    """
    if prefix or not value.strip() or not engine.enabled():
        return queryset, lexical
    try:
        vector = engine.embed_query(value)
    except engine.EmbeddingsUnavailable as e:
        logger.warning("Embeddings unavailable (%s); search %r is substring-only", e, value)
        return queryset, lexical
    if vector is None:
        return queryset, lexical

    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=4).hexdigest()
    distance, rank = f"_search_distance_{digest}", f"_search_rank_{digest}"
    if distance not in queryset.query.annotations:
        queryset = queryset.annotate(
            **{
                distance: CosineDistance("embedding", vector),
                rank: Case(When(lexical, then=Value(0)), default=Value(1), output_field=IntegerField()),
            }
        )
    queryset = queryset.order_by(rank, F(distance).asc(nulls_last=True), "pk")

    # NULL embeddings give a NULL distance, which is never below the threshold: unembedded rows
    # and rows of another model are excluded from the vector leg and reachable lexically only.
    semantic = Q(embedding_model=engine.model_id()) & Q(**{f"{distance}__lt": engine.distance_threshold()})
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

    Rows embedded by a different model are excluded, as in :func:`hybrid_search`: their
    vectors come from another space and their distances are not comparable.
    """
    if vector is None or not engine.enabled():
        return queryset, Q(pk__in=[])

    if "_neighbour_distance" not in queryset.query.annotations:
        queryset = queryset.annotate(_neighbour_distance=CosineDistance("embedding", vector))
    queryset = queryset.order_by("_neighbour_distance", "pk")

    predicate = Q(embedding_model=engine.model_id()) & Q(embedding__isnull=False)
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
