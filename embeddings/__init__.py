"""Semantic search over description fields: pgvector columns filled by a static embedding model.

Vendored byte-identical into every service that offers a semantic ``search`` (rekuest, mikro),
like ``kanne_server``. It is a plain package, not a Django app: it owns no concrete model and no
migration. A service opts a model in by mixing :class:`embeddings.models.EmbeddedDescriptionMixin`
into it, splicing :func:`embeddings.models.embedding_indexes` into its ``Meta.indexes`` and
writing the migration that adds the two columns (plus ``VectorExtension``).

The contract, in one place:

* **The model is configuration.** ``settings.EMBEDDINGS`` names the model2vec model and its
  width; the ``vector(N)`` column is that width and the row records which model filled it
  (``embedding_model``). Search only compares vectors of the configured model.
* **Rows embed themselves.** ``save()`` recomputes the vector when the source text changed or
  the row was filled by another model. Nothing else has to remember to call anything.
* **Stale rows heal in-process.** A configured model change leaves rows behind with another
  ``embedding_model``; :mod:`embeddings.healer` re-embeds them in row-locked batches from
  inside the serving process, so any number of replicas can run it and no command or cron
  exists. Until healed, such rows are found by the lexical leg of ``search`` only.
* **Search is hybrid.** :func:`embeddings.search.hybrid_search` ORs the existing lexical
  predicate with "cosine distance below the threshold", ranks lexical hits first and then by
  distance, and degrades to lexical-only whenever embeddings are off or unavailable.
"""

from embeddings.engine import (
    EmbeddingsUnavailable,
    dimensions,
    distance_threshold,
    embed_query,
    embed_texts,
    enabled,
    model_id,
    source_text,
    warm_up,
)

__all__ = [
    "EmbeddingsUnavailable",
    "dimensions",
    "distance_threshold",
    "embed_query",
    "embed_texts",
    "enabled",
    "model_id",
    "source_text",
    "warm_up",
]
