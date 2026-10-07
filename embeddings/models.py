"""The abstract mixin that gives a model an embedding of its name and description."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from django.db import models
from pgvector.django import VectorField

from embeddings import engine

logger = logging.getLogger(__name__)

#: The column the mixin adds; what ``save()`` appends to ``update_fields``.
EMBEDDING_FIELDS: tuple[str, ...] = ("embedding",)

#: "The source text as loaded is not known" -- a source field was deferred, or the instance
#: was never loaded from the database. ``save()`` then recomputes rather than guesses.
_UNKNOWN = object()


class EmbeddedDescriptionMixin(models.Model):
    """Mix into a model with ``name`` and ``description`` to keep a vector of them.

    ``embedding`` is the unit-length vector of :func:`embeddings.engine.source_text`, by the
    model of this release (:data:`embeddings.engine.MODEL`). A row recomputes it in ``save()``
    when it is new or when its source text changed since it was loaded. A blank source stores
    ``NULL``. There is deliberately no index on the column: the hybrid ``search`` predicate
    (lexical OR semantic, org-scoped) cannot use an ANN index, and an exact scan is the correct
    answer at the sizes these tables reach.
    """

    embedding_source_fields: ClassVar[tuple[str, ...]] = ("name", "description")

    #: The source text this instance was loaded with (set by ``from_db``), ``_UNKNOWN`` otherwise.
    _embedding_source_seen: object = _UNKNOWN

    embedding = VectorField(
        dimensions=engine.DIMENSIONS,
        null=True,
        blank=True,
        editable=False,
        help_text="Unit-length embedding of name + description; NULL when there is no text to embed",
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

    def embedding_needs_refresh(self) -> bool:
        """Whether ``save()`` should recompute the vector: the row is new, or its text changed."""
        if self._state.adding:
            return True
        source = self.embedding_source_text()
        seen = getattr(self, "_embedding_source_seen", _UNKNOWN)
        if seen is _UNKNOWN:
            return True
        if source is None:
            return self.embedding is not None
        return self.embedding is None or source != seen

    def refresh_embedding(self) -> None:
        """Recompute ``embedding`` from the current source text.

        Does not save. A failure to load the model is logged and leaves the row without a
        vector: the write itself must not fail because the model did. Such a row is found by
        the lexical leg of ``search`` only, until a later save of it embeds. Weights of another
        model or width are a broken image and propagate.
        """
        source = self.embedding_source_text()
        try:
            self.embedding = engine.embed_texts([source])[0] if source is not None else None
        except engine.EmbeddingsUnavailable as e:
            logger.warning("Could not embed %s %s (%s); saving it without a vector", type(self).__name__, self.pk, e)
            self.embedding = None
            return
        self._embedding_source_seen = source

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Embed before writing whenever the source text may have changed."""
        update_fields = kwargs.get("update_fields")
        touches_source = update_fields is None or any(field in update_fields for field in self.embedding_source_fields)
        if engine.enabled() and touches_source and self.embedding_needs_refresh():
            self.refresh_embedding()
            if update_fields is not None:
                kwargs["update_fields"] = list(dict.fromkeys([*update_fields, *EMBEDDING_FIELDS]))
        super().save(*args, **kwargs)
        if update_fields is None or touches_source:
            self._embedding_source_seen = self.embedding_source_text()
