"""The embedding model: loading, text -> vector, and the settings that describe it.

The only module that imports ``model2vec``. Everything reads ``settings.EMBEDDINGS`` at call
time (never at import), so ``override_settings`` works in tests and a process picks up its
configuration when Django is ready, not when this module happens to be imported.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from model2vec import StaticModel

logger = logging.getLogger(__name__)

#: Written next to baked weights by the Dockerfile so a process can tell which model a
#: ``model_path`` directory holds, and refuse a configuration that names another one.
MODEL_ID_FILENAME = "MODEL_ID"

_load_lock = threading.Lock()


class EmbeddingsUnavailable(RuntimeError):
    """The configured model cannot be loaded (missing weights, no network, wrong width).

    Raised from :func:`embed_texts` / :func:`embed_query` so a caller that can degrade (the
    ``search`` filter) does so explicitly, and one that cannot (the row's ``save()``) can log
    and leave the row for the healer.
    """


def _settings() -> dict[str, Any]:
    return getattr(settings, "EMBEDDINGS", {})


def enabled() -> bool:
    """Whether rows embed themselves and ``search`` has a semantic leg."""
    return bool(_settings().get("ENABLED", False))


def model_id() -> str:
    """The configured model2vec model id, verbatim; the value written to ``embedding_model``."""
    return str(_settings()["MODEL"])


def model_path() -> str | None:
    """Directory holding the weights of :func:`model_id` (``save_pretrained`` layout), if baked."""
    value = _settings().get("MODEL_PATH")
    return str(value) if value else None


def dimensions() -> int:
    """The configured vector width. Must match both the model and the ``vector(N)`` column."""
    return int(_settings()["DIMENSIONS"])


def distance_threshold() -> float:
    """Cosine distance above which a row stops counting as a semantic ``search`` hit."""
    return float(_settings()["DISTANCE_THRESHOLD"])


def sweep_batch_size() -> int:
    """Rows the healer re-embeds per batch."""
    return int(_settings().get("SWEEP_BATCH_SIZE", 200))


def sweep_interval() -> float:
    """Seconds between healer passes (services without their own sweep loop)."""
    return float(_settings().get("SWEEP_INTERVAL", 30))


@lru_cache(maxsize=1)
def _load_model_cached(path: str | None, name: str, width: int) -> StaticModel:
    """Load once per (path, model, width); the arguments make a config change a cache miss."""
    from model2vec import StaticModel

    if path is not None:
        stamp = os.path.join(path, MODEL_ID_FILENAME)
        if os.path.exists(stamp):
            with open(stamp, encoding="utf-8") as handle:
                baked = handle.read().strip()
            if baked != name:
                raise ImproperlyConfigured(f"EMBEDDINGS.MODEL_PATH {path!r} holds the weights of {baked!r} but EMBEDDINGS.MODEL is {name!r}. Rebuild the image for the new model, or point MODEL_PATH elsewhere.")
        model = StaticModel.from_pretrained(path)
    else:
        # ``force_download`` defaults to True upstream, which would re-fetch the weights on
        # every process start; the hub cache is exactly what a dev box or CI runner wants.
        model = StaticModel.from_pretrained(name, force_download=False)
    if int(model.dim) != width:
        raise ImproperlyConfigured(f"Embedding model {name!r} produces {model.dim}-wide vectors but EMBEDDINGS.DIMENSIONS is {width}. The vector column is that width too: changing the model's width is a migration (see CONFIG.md).")
    logger.info("Embedding model %s loaded (%d dims)%s", name, width, f" from {path}" if path else "")
    return model


def _model() -> StaticModel:
    """The loaded model, or :class:`EmbeddingsUnavailable` with the cause chained."""
    key = (model_path(), model_id(), dimensions())
    try:
        # ``lru_cache`` is not atomic: two worker threads would both load. The lock is only
        # contended during the first load of a process.
        with _load_lock:
            return _load_model_cached(*key)
    except ImproperlyConfigured:
        raise
    except Exception as exc:  # weights missing, no network, corrupt files, ...
        raise EmbeddingsUnavailable(f"Embedding model {key[1]!r} could not be loaded: {exc}") from exc


def reset() -> None:
    """Drop the loaded model so the next call reloads from the current settings (tests)."""
    with _load_lock:
        _load_model_cached.cache_clear()


def warm_up() -> None:
    """Load the model now rather than on the first row or query.

    Raises :class:`django.core.exceptions.ImproperlyConfigured` on a width mismatch and
    :class:`EmbeddingsUnavailable` when the weights cannot be loaded. A no-op when disabled.
    """
    if enabled():
        _model()


def source_text(*texts: str | None) -> str | None:
    """The text a row is embedded from: its source fields, each stripped, newline-joined.

    ``None`` when they are all blank -- such a row has no vector (never a zero vector).

    Variadic, rather than ``(name, description)``: the mixin splats a model's
    ``embedding_source_fields`` into this, and models that carry their text in one field
    (an app's identifier, a repo's name) or in three raised ``TypeError`` on their first save.
    """
    parts = [part.strip() for part in texts if part and part.strip()]
    return "\n".join(parts) if parts else None


def embed_texts(texts: Sequence[str]) -> list[list[float] | None]:
    """Embed each text; unit-length vectors as plain lists, ``None`` where the model gave zeros.

    A static model can emit an all-zero vector for text made only of unknown tokens (or for
    the empty string). Cosine distance to a zero vector is NaN, so such a row must store
    ``NULL`` and such a query must not run the semantic leg.
    """
    if not texts:
        return []
    model = _model()
    matrix = np.asarray(model.encode(list(texts), show_progress_bar=False, use_multiprocessing=False), dtype=np.float32)
    if matrix.ndim == 1:  # a single text comes back as one row
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1)
    out: list[list[float] | None] = []
    for row, norm in zip(matrix, norms, strict=True):
        if not np.isfinite(norm) or norm == 0.0:
            out.append(None)
        else:
            out.append((row / norm).astype(float).tolist())
    return out


def embed_query(text: str) -> list[float] | None:
    """The vector for a search query, or ``None`` when the text is blank or embeds to zeros."""
    if not text or not text.strip():
        return None
    return embed_texts([text.strip()])[0]


def column_dimensions(connection: Any, table: str, column: str) -> int | None:
    """The declared width of ``table.column``'s ``vector(N)`` type, or ``None`` if absent.

    ``None`` also when the type carries no modifier (a bare ``vector`` column).
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = %s AND a.attname = %s AND NOT a.attisdropped",
            [table, column],
        )
        row = cursor.fetchone()
    if row is None:
        return None
    declared = str(row[0])  # e.g. "vector(256)"
    if not declared.startswith("vector(") or not declared.endswith(")"):
        return None
    return int(declared[len("vector(") : -1])
