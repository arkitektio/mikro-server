"""Move the names people typed onto lenses to the positions those lenses pin.

`Lens.name` stops being edited and becomes a cache of what a lens' pinned positions (and its
cut) are called. A name someone gave a lens that pins positions -- `c[1:2]` called "GFP" -- is
kept by writing it as a `PositionLabel` at the anchor of those positions, unless a person
already named them; the cache is then recomputed by `merge_duplicate_lenses`, which the setup
runs after the migrations. Only a lens that selects *nothing but* pinned positions passes its
name on: a crop `c[1:2], y[0:32]` called "nucleus 3" names a region, and moving that to
`{c: 1}` would call all of channel 1 "nucleus 3". Such a name -- and one on a lens that pins
nothing, or whose positions an earlier lens already named -- has nowhere to stand and is cleared
by that recomputation; how many there are is printed here.

Alone in its migration, between the one that adds `PositionLabel` and anything after. The pins
arithmetic is inlined: a migration does not import the service's code.
"""

from django.db import migrations

# The lens kinds whose container carries anchors: (lens model, its container field, the anchor's field).
_KINDS = (("ArrayLens", "dataset", "dataset"), ("TableLens", "table_dataset", "table"), ("SparseLens", "sparse_dataset", "sparse"))


def _pins(selection: object) -> dict:
    """The positions a stored selection holds its axes to (`core.logic.coords.pins`, as of this migration)."""
    pinned: dict = {}
    for entry in selection if isinstance(selection, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("axis"), str):
            continue
        if "start" in entry or "stop" in entry:
            start, stop = entry.get("start"), entry.get("stop")
            if isinstance(start, int) and isinstance(stop, int) and stop - start == 1:
                pinned[entry["axis"]] = start
        else:
            low, high = entry.get("min"), entry.get("max")
            if isinstance(low, int | float) and not isinstance(low, bool) and low == high:
                pinned[entry["axis"]] = low
    return pinned


def forwards(apps, schema_editor) -> None:  # noqa: ANN001 - Django's migration signature
    CoordinateAnchor = apps.get_model("core", "CoordinateAnchor")
    PositionLabel = apps.get_model("core", "PositionLabel")
    moved = dropped = 0
    for model_name, container_field, anchor_field in _KINDS:
        model = apps.get_model("core", model_name)
        selection_field = "slices" if model_name == "ArrayLens" else "windows"
        for lens in model.objects.exclude(name__isnull=True).exclude(name="").order_by("pk").iterator():
            name = (lens.name or "").strip()
            selection = getattr(lens, selection_field)
            pinned = _pins(selection)
            if not name:
                continue
            if not pinned or len(pinned) != len(selection if isinstance(selection, list) else []):
                dropped += 1
                continue
            container_id = getattr(lens, f"{container_field}_id")
            anchor = CoordinateAnchor.objects.filter(coordinates=pinned, **{f"{anchor_field}_id": container_id}).first()
            if anchor is None:
                anchor = CoordinateAnchor.objects.create(coordinates=pinned, organization_id=lens.organization_id, **{f"{anchor_field}_id": container_id})
            if PositionLabel.objects.filter(anchor=anchor).exists():
                dropped += 1
                continue
            PositionLabel.objects.create(anchor=anchor, label=name, creator_id=lens.creator_id)
            moved += 1
    for model_name in ("MeshLens", "NetworkLens", "FixtureLens", "AnnotationLens"):
        dropped += apps.get_model("core", model_name).objects.exclude(name__isnull=True).exclude(name="").count()
    if moved or dropped:
        print(f"\n  lens names: {moved} moved to the positions their lens pins; {dropped} on crops, windows or already-named positions, cleared by merge_duplicate_lenses")


class Migration(migrations.Migration):
    dependencies = [("core", "0002_position_labels")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
