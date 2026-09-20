"""System checks: the configured width must be the model's width and the column's width.

Import this module from the service's ``AppConfig.ready()`` to register them. The database
check is tagged so ``manage.py migrate`` runs it -- and every service runs ``migrate`` at boot,
which makes a mismatch fail the boot rather than the first search.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.checks import Error, Tags, Warning, register
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from embeddings import engine
from embeddings.models import EmbeddedDescriptionMixin


def embedded_models() -> list[type[EmbeddedDescriptionMixin]]:
    """Every concrete installed model that mixes in :class:`EmbeddedDescriptionMixin`."""
    return [model for model in apps.get_models() if issubclass(model, EmbeddedDescriptionMixin)]


@register(Tags.compatibility)
def check_model_dimensions(app_configs: Any, **kwargs: Any) -> list[Error | Warning]:
    """``embeddings.E001``: the model does not produce ``EMBEDDINGS.DIMENSIONS``-wide vectors."""
    if not engine.enabled():
        return []
    try:
        engine.warm_up()
    except ImproperlyConfigured as exc:
        return [Error(str(exc), id="embeddings.E001")]
    except engine.EmbeddingsUnavailable as exc:
        # Not fatal: rows embed on the healer's next pass once the weights are reachable, and
        # ``search`` degrades to its lexical leg meanwhile. Say so loudly, though.
        return [Warning(f"{exc}. Rows will not be embedded and search is substring-only until the model loads.", id="embeddings.W001")]
    return []


@register(Tags.database)
def check_column_dimensions(app_configs: Any, databases: Any = None, **kwargs: Any) -> list[Error]:
    """``embeddings.E002``: a ``vector(N)`` column whose N is not ``EMBEDDINGS.DIMENSIONS``.

    Skipped for columns that do not exist yet (the migration adding them has not run).
    """
    if not databases:
        return []
    want = engine.dimensions()
    errors: list[Error] = []
    for alias in databases:
        connection = connections[alias]
        if connection.vendor != "postgresql":
            continue
        for model in embedded_models():
            have = engine.column_dimensions(connection, model._meta.db_table, "embedding")
            if have is not None and have != want:
                errors.append(
                    Error(
                        f"{model._meta.label}.embedding is vector({have}) in the database but EMBEDDINGS.DIMENSIONS is {want}. Changing the model's width is a migration: null the column, alter it to vector({want}), and let the healer refill it (see CONFIG.md).",
                        id="embeddings.E002",
                    )
                )
    return errors
