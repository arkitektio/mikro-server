"""Re-embed rows filled by another model (or none), from inside the serving process.

The stale set is ``embedding_model != EMBEDDINGS.MODEL`` -- rows written before embeddings were
enabled, rows whose model failed to load at write time, and everything after the configured
model changes. Each batch is a row-locked claim (``select_for_update(skip_locked=True)``) so N
replicas sweeping at once take disjoint rows and a replica dying mid-batch releases its share.
Rows are written with ``bulk_update``: no ``save()``, so no signals, no history rows, no
broadcasts -- a re-embed is not an edit.

No management command and no cron. rekuest runs :func:`reembed_stale` from its reaper tick;
a service without a sweep loop starts :func:`ensure_healer_started` from its ASGI entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence
from typing import Any

from channels.db import database_sync_to_async
from django.db import models, transaction

from embeddings import engine
from embeddings.models import EMBEDDING_FIELDS, EmbeddedDescriptionMixin

logger = logging.getLogger(__name__)

_healer_task: asyncio.Task[None] | None = None


def stale_queryset(model_cls: type[EmbeddedDescriptionMixin]) -> models.QuerySet[Any]:
    """Rows of ``model_cls`` whose vector was not produced by the configured model."""
    return model_cls._default_manager.exclude(embedding_model=engine.model_id())


def reembed_batch(model_cls: type[EmbeddedDescriptionMixin], batch_size: int | None = None) -> int:
    """Claim and re-embed up to ``batch_size`` stale rows; the number of rows written.

    A no-op (one indexed query) when nothing is stale, and when embeddings are disabled.
    """
    if not engine.enabled():
        return 0
    size = batch_size if batch_size is not None else engine.sweep_batch_size()
    fields = list(model_cls.embedding_source_fields)
    with transaction.atomic():
        rows = list(stale_queryset(model_cls).select_for_update(skip_locked=True, of=("self",)).only("pk", *fields, *EMBEDDING_FIELDS).order_by("pk")[:size])
        if not rows:
            return 0
        sources = [row.embedding_source_text() for row in rows]
        vectors = engine.embed_texts([source for source in sources if source is not None])
        current = engine.model_id()
        vector_iter = iter(vectors)
        for row, source in zip(rows, sources, strict=True):
            row.embedding = next(vector_iter) if source is not None else None
            row.embedding_model = current
        model_cls._default_manager.bulk_update(rows, list(EMBEDDING_FIELDS))
    return len(rows)


def reembed_stale(model_cls: type[EmbeddedDescriptionMixin], batch_size: int | None = None, max_batches: int | None = None) -> int:
    """Drain the stale rows of one model, ``max_batches`` batches at most; rows written."""
    total = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        try:
            done = reembed_batch(model_cls, batch_size)
        except engine.EmbeddingsUnavailable as e:
            # Expected until the model loads: one line, no traceback.
            logger.warning("Embedding model unavailable (%s); %s rows stay stale until it loads", e, model_cls.__name__)
            break
        total += done
        batches += 1
        if done < (batch_size if batch_size is not None else engine.sweep_batch_size()):
            break
    if total:
        logger.info("Re-embedded %d %s rows", total, model_cls.__name__)
    return total


def reembed_all(model_classes: Sequence[type[EmbeddedDescriptionMixin]], batch_size: int | None = None, max_batches: int | None = None) -> int:
    """:func:`reembed_stale` over several models; the total rows written."""
    return sum(reembed_stale(model_cls, batch_size, max_batches) for model_cls in model_classes)


async def run_healer_loop(model_classes: Sequence[type[EmbeddedDescriptionMixin]], interval: float | None = None) -> None:
    """Sweep forever, ``interval`` seconds apart; the first pass runs at once.

    One bad iteration never ends the loop. Cancellation ends it.
    """
    await asyncio.sleep(random.uniform(0, 0.5))  # replicas started together should not tick in lockstep
    while True:
        try:
            await database_sync_to_async(reembed_all)(model_classes, max_batches=5)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.error("Embedding healer pass failed; continuing.", exc_info=True)
        await asyncio.sleep(interval if interval is not None else engine.sweep_interval())


def ensure_healer_started(model_classes: Sequence[type[EmbeddedDescriptionMixin]], interval: float | None = None) -> None:
    """Start :func:`run_healer_loop` once per process; a cheap no-op on every later call."""
    global _healer_task
    if not engine.enabled():
        return
    if _healer_task is not None and not _healer_task.done():
        return
    _healer_task = asyncio.ensure_future(run_healer_loop(model_classes, interval))
