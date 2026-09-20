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
    except engine.EmbeddingsUnavailable:
        logger.warning("Embeddings unavailable; search %r is substring-only", value, exc_info=True)
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
