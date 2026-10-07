"""Semantic search over description fields: pgvector columns filled by a static embedding model.

Vendored byte-identical into every service that offers a semantic ``search`` (rekuest, mikro),
like ``kanne_server``. It is a plain package, not a Django app: it owns no concrete model and no
migration. A service opts a model in by mixing :class:`embeddings.models.EmbeddedDescriptionMixin`
into it and writing the migration that adds the column (plus ``VectorExtension``).

The contract, in one place:

* **The model is part of the release.** :data:`embeddings.engine.MODEL` names the model2vec
  model and :data:`embeddings.engine.DIMENSIONS` its width; the image carries the weights and
  the ``vector(N)`` column is that width. Neither is configuration: another model only ever
  ships as another image, whose migrations re-embed the rows, so every vector in a database
  is the same model's and search compares them without asking.
* **Rows embed themselves.** ``save()`` recomputes the vector when the row is new or its
  source text changed. Nothing else has to remember to call anything. A row saved while the
  model could not be loaded has no vector and is found by the lexical leg of ``search`` only.
* **The server loads the model, a command does not.** The serving process warms the model up
  as it starts (``<service>_server/asgi.py``); ``manage.py migrate`` and every other
  management command leave it alone.
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
