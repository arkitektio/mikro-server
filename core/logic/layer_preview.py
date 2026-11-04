"""Building a layer without writing it: what ``previewLayer`` answers with.

A preview is the row a ``create*Layer`` mutation WOULD write, resolved through the same
``Layer`` type -- so a client draws it from the very fields (``asAffine``, ``pathToWorld``,
the kind's render columns) it would read off a saved layer, and "keep it" is the same
create sent for real. There is no second recipe: every create writes its row through
:func:`persist_layer`, and inside :func:`previewing` that call builds the instance and
saves nothing. Everything before it -- the org scope, ``assert_renderable``,
``assert_placeable_in``, the kind's defaults -- runs unchanged, so a preview is refused
for exactly the reasons the create would be.

An unsaved instance cannot carry a primary key of its own, and every resolver that walks a
relation needs one. It gets :data:`PREVIEW_PK` -- negative, so it can never name a row --
and its one many-to-many the resolvers read (``channel_lenses``, which an image layer's
graph indexes) is answered from the prefetch cache rather than a through table that has
no row for it. A preview is never saved: :func:`persist_layer` is the only writer here, and
in preview mode it does not write.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Iterator

from django.db import transaction

from core import models

#: The primary key a previewed layer carries: negative, so it never names a saved row.
PREVIEW_PK = -1

_previewing: ContextVar[bool] = ContextVar("mikro_layer_preview", default=False)


@contextmanager
def previewing() -> Iterator[None]:
    """Inside this, :func:`persist_layer` builds the row and writes nothing."""
    token = _previewing.set(True)
    try:
        yield
    finally:
        _previewing.reset(token)


def is_previewing() -> bool:
    """Whether the current create is a preview."""
    return _previewing.get()


def persist_layer(*, channel_lens_ids: Iterable[int] | None = None, **fields) -> "models.Layer":
    """Write a new layer row -- or, while :func:`previewing`, build the row it would be.

    ``channel_lens_ids`` is the image kind's index of the lenses its render graph names;
    the row and that index are one write, as they always were.
    """
    if not is_previewing():
        if channel_lens_ids is None:
            return models.Layer.objects.create(**fields)
        with transaction.atomic():
            layer = models.Layer.objects.create(**fields)
            layer.channel_lenses.set(channel_lens_ids)
        return layer

    layer = models.Layer(**fields)
    layer.pk = PREVIEW_PK
    # What `layer.channel_lenses.all()` answers for a row with no through-table entries.
    layer._prefetched_objects_cache = {
        "channel_lenses": models.ArrayLens.objects.filter(pk__in=list(channel_lens_ids or [])),
    }
    return layer
