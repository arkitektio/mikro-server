"""What a lens is called: the labels at the positions it pins, and the shape it was cut to.

A lens is one row per selection, so a name typed onto a row names only that row: calling
``c[1:2]`` "GFP" left the crop ``c[1:2], y[0:32]`` and every scene bootstrapped through
channel 1 none the wiser. The name lives where the data does instead -- at the coordinate
anchor of the positions a lens pins (:func:`core.logic.coords.pins`) -- and every lens
pinning them reads it:

* **Which labels hold.** An anchor of the lens' container whose coordinates are non-empty and
  all among the lens' pins (the same axis at the same position). It is global along every
  axis it omits, so it holds over the whole of the lens. ``{c: 1, z: 5}`` does *not* hold
  over ``c[1:2]``: the lens keeps every z, the label speaks of one.
* **The most specific wins.** An anchor whose axes are a strict subset of another holding
  anchor's says less about the lens and is dropped: ``{c: 1, t: 5}`` "after washout" over
  ``{c: 1}`` "GFP".
* **A person over a record.** On one anchor a :class:`~core.models.PositionLabel` beats the
  :class:`~core.models.ChannelLabel` ingest wrote; deleting it brings the record back.
* **A region by its shape.** An array lens cut to an annotation (``LensCut``) adds the name
  of the first shape it was cut to.

The result is cached on ``Lens.name`` -- search and the pickers filter on it in SQL -- and,
its inputs being mutable, refreshed by whoever writes one: the spoke and cut signals in
:mod:`core.signals`, ``_mint`` for a lens being made, and ``merge_duplicate_lenses`` for drift.
The container's own name is never part of it, so renaming a dataset touches no lens.
"""

from collections.abc import Iterable, Sequence

from core import models
from core.logic import coords as coords_logic

#: The lens kinds whose container carries anchors, and the anchor's foreign key to it.
ANCHOR_FIELD: dict[type[models.Lens], str] = {
    models.ArrayLens: "dataset",
    models.TableLens: "table",
    models.SparseLens: "sparse",
}

#: The anchor's foreign key, by the container model.
_ANCHOR_FIELD_BY_CONTAINER: dict[type, str] = {
    models.ArrayDataset: "dataset",
    models.TableDataset: "table",
    models.SparseDataset: "sparse",
}

SEPARATOR = " · "


def anchor_label(anchor: models.CoordinateAnchor) -> str | None:
    """The name an anchor gives its coordinates: a person's over ingest's, or None."""
    for spoke_name in ("position_label", "channel_label"):
        try:
            spoke = getattr(anchor, spoke_name)
        except (models.PositionLabel.DoesNotExist, models.ChannelLabel.DoesNotExist):
            continue
        text = (spoke.label or "").strip()
        if text:
            return text
    return None


def labelled_anchors(container: object) -> list[models.CoordinateAnchor]:
    """A container's anchors that carry a name and pin something, with both label spokes loaded."""
    field = _ANCHOR_FIELD_BY_CONTAINER.get(type(container))
    if field is None:
        return []
    query = models.CoordinateAnchor.objects.filter(**{field: container}).exclude(coordinates={}).select_related("position_label", "channel_label").order_by("pk")
    return [anchor for anchor in query if anchor_label(anchor) is not None]


def holding_anchors(pinned: dict[str, int | float], anchors: Iterable[models.CoordinateAnchor]) -> list[models.CoordinateAnchor]:
    """The labelled anchors that name a selection with these pins, most specific only, in the order of the pins' axes."""
    if not pinned:
        return []
    holding = [
        anchor
        for anchor in anchors
        if isinstance(anchor.coordinates, dict) and anchor.coordinates and all(axis in pinned and pinned[axis] == value for axis, value in anchor.coordinates.items())
    ]
    keys = [frozenset(anchor.coordinates) for anchor in holding]
    specific = [anchor for anchor, axes in zip(holding, keys) if not any(axes < other for other in keys)]
    order = list(pinned)
    return sorted(specific, key=lambda anchor: sorted(order.index(axis) for axis in anchor.coordinates))


def compose(pinned: dict[str, int | float], anchors: Iterable[models.CoordinateAnchor], cut: str | None) -> str | None:
    """The name: the holding labels in axis order, then the shape's, or None when nothing names the selection."""
    parts = [anchor_label(anchor) for anchor in holding_anchors(pinned, anchors)]
    if cut and cut.strip():
        parts.append(cut.strip())
    names = [part for part in parts if part]
    return SEPARATOR.join(names) if names else None


def _cut_names(lens_ids: Sequence[int]) -> dict[int, str]:
    """The name of the first shape each array lens was cut to, by lens id."""
    names: dict[int, str] = {}
    for lens_id, name in models.LensCut.objects.filter(lens_id__in=lens_ids).order_by("created_at", "id").values_list("lens_id", "annotation__name"):
        if lens_id not in names and name and name.strip():
            names[lens_id] = name
    return names


def resolve_name(lens: models.Lens) -> str | None:
    """What one lens is called now. Works on an unsaved row, which has no cut yet."""
    real = lens._real()
    pinned = coords_logic.pins(real.selection)
    anchors = labelled_anchors(real.container) if pinned and type(real) in ANCHOR_FIELD else []
    cut = _cut_names([real.pk]).get(real.pk) if real.pk is not None and isinstance(real, models.ArrayLens) else None
    return compose(pinned, anchors, cut)


def _store(lens: models.Lens, name: str | None, *, announce: bool) -> bool:
    """Write a changed name: a save (described again, and announced as an update) or, from a job, a silent update."""
    if lens.name == name:
        return False
    lens.name = name
    if announce:
        lens.save(update_fields=["name"])
    else:
        type(lens).objects.filter(pk=lens.pk).update(name=name)
    return True


def refresh_container(container: object, *, announce: bool = True) -> int:
    """Name every lens over a container again, writing the ones that changed. How many did."""
    kind = models.Lens.KIND_BY_CONTAINER.get(type(container))
    if kind is None:
        return 0
    lenses = list(kind.objects.filter(**{kind.CONTAINER_FIELD: container}))
    pinned = {lens.pk: coords_logic.pins(lens.selection) for lens in lenses}
    # An ingest writes its labels before any lens but the whole one exists: nothing pins, and
    # the anchors -- one per plane, perhaps, and this runs once per label -- are not read.
    anchors = labelled_anchors(container) if kind in ANCHOR_FIELD and any(pinned.values()) else []
    cuts = _cut_names([lens.pk for lens in lenses]) if kind is models.ArrayLens and lenses else {}
    return sum(_store(lens, compose(pinned[lens.pk], anchors, cuts.get(lens.pk)), announce=announce) for lens in lenses)


def refresh_lenses(lens_ids: Iterable[int], *, announce: bool = True) -> int:
    """Name these array lenses again: a shape they were cut to was renamed, or a cut came or went."""
    changed = 0
    for lens in models.ArrayLens.objects.filter(pk__in=list(lens_ids)).select_related("dataset"):
        changed += _store(lens, resolve_name(lens), announce=announce)
    return changed
