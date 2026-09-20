"""The abstract mixin that gives a model an embedding of its name and description."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from embeddings import engine

logger = logging.getLogger(__name__)

#: The two columns the mixin adds; what ``save()`` appends to ``update_fields`` and what the
#: healer's ``bulk_update`` writes.
EMBEDDING_FIELDS: tuple[str, ...] = ("embedding", "embedding_model")

#: "The source text as loaded is not known" -- a source field was deferred, or the instance
#: was never loaded from the database. ``save()`` then recomputes rather than guesses.
_UNKNOWN = object()


def _configured_dimensions() -> int:
    # Read at class-definition time: the column width has to be known to ``makemigrations``,
    # so it is configuration, and the system checks make it agree with the model and the DB.
    return int(getattr(settings, "EMBEDDINGS", {}).get("DIMENSIONS", 256))


def embedding_indexes(prefix: str) -> list[models.Index]:
    """The indexes a concrete model splices into its ``Meta.indexes``.

    Django does not merge ``Meta`` from an abstract mixin, so this is explicit. Names must stay
    within Django's 30-character limit, so ``prefix`` is short. Only a btree on
    ``embedding_model``: the healer's "anything stale?" probe hits it and is a no-op the rest of
    the time. There is deliberately no ANN index: the hybrid ``search`` predicate (lexical OR
    semantic, org-scoped) cannot use one, and an exact scan is the correct answer at the sizes
    these tables reach.
    """
    return [models.Index(fields=["embedding_model"], name=f"{prefix}_emb_model_idx")]


class EmbeddedDescriptionMixin(models.Model):
    """Mix into a model with ``name`` and ``description`` to keep a vector of them.

    ``embedding`` is the unit-length vector of :func:`embeddings.engine.source_text` and
    ``embedding_model`` names the model that produced it. A row recomputes both in ``save()``
    when its source text changed since it was loaded, when it was filled by another model, or
    when it is new. A blank source stores ``NULL`` with the current model id, so it is complete
    and never re-claimed by the healer.
    """

    embedding_source_fields: ClassVar[tuple[str, ...]] = ("name", "description")

    #: The source text this instance was loaded with (set by ``from_db``), ``_UNKNOWN`` otherwise.
    _embedding_source_seen: object = _UNKNOWN

    embedding = VectorField(
        dimensions=_configured_dimensions(),
        null=True,
        blank=True,
        editable=False,
        help_text="Unit-length embedding of name + description, by the model named in embedding_model; NULL when there is no text to embed",
    )
    embedding_model = models.CharField(
        max_length=200,
        blank=True,
        default="",
        editable=False,
        help_text="The embedding model that produced `embedding`. Rows whose value differs from the configured model are re-embedded in-process and are excluded from vector search until then",
    )

    class Meta:
        """Abstract: the columns land on the concrete model's table."""

        abstract = True

    @classmethod
    def from_db(cls, db: str | None, field_names: Any, values: Any) -> EmbeddedDescriptionMixin:
        """Remember the source text as loaded, so ``save()`` can tell whether it changed."""
        instance = super().from_db(db, field_names, values)
        instance._embedding_source_seen = instance._embedding_source_if_loaded()
        return instance

    def _embedding_source_if_loaded(self) -> str | None | object:
        """The source text, or the sentinel ``_UNKNOWN`` when a source field is deferred."""
        deferred = self.get_deferred_fields()
        if any(field in deferred for field in self.embedding_source_fields):
            return _UNKNOWN
        return self.embedding_source_text()

    def embedding_source_text(self) -> str | None:
        """The text this row embeds (name + description)."""
        return engine.source_text(*(getattr(self, field, None) for field in self.embedding_source_fields))

    def embedding_is_stale(self) -> bool:
        """Whether ``save()`` should recompute the vector."""
        if self._state.adding or self.embedding_model != engine.model_id():
            return True
        source = self.embedding_source_text()
        seen = getattr(self, "_embedding_source_seen", _UNKNOWN)
        if seen is _UNKNOWN:
            return True
        if source is None:
            return self.embedding is not None
        return self.embedding is None or source != seen

    def refresh_embedding(self) -> None:
        """Recompute ``embedding`` / ``embedding_model`` from the current source text.

        Does not save. A failure to load the model is logged and leaves ``embedding_model``
        empty: the write itself must not fail because the model did, and the healer will
        retry the row. A width mismatch is a configuration error and propagates.
        """
        source = self.embedding_source_text()
        try:
            self.embedding = engine.embed_texts([source])[0] if source is not None else None
        except engine.EmbeddingsUnavailable:
            logger.warning("Could not embed %s %s; leaving it for the healer", type(self).__name__, self.pk, exc_info=True)
            self.embedding = None
            self.embedding_model = ""
            return
        self.embedding_model = engine.model_id()
        self._embedding_source_seen = source

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Embed before writing whenever the source text (or the model) may have changed."""
        update_fields = kwargs.get("update_fields")
        touches_source = update_fields is None or any(field in update_fields for field in self.embedding_source_fields)
        if engine.enabled() and touches_source and self.embedding_is_stale():
            self.refresh_embedding()
            if update_fields is not None:
                kwargs["update_fields"] = list(dict.fromkeys([*update_fields, *EMBEDDING_FIELDS]))
        super().save(*args, **kwargs)
        if update_fields is None or touches_source:
            self._embedding_source_seen = self.embedding_source_text()
