"""Every colour or filter picker entry that read a slice of a sparse matrix by dataset and positions now names the lens that is that slice.

A slice is a selection and a selection is a lens, so a SPARSE entry's ``dataset`` + ``at``
become ``lens``: the sparse dataset's lens whose windows pin each named position (``min ==
max``), made the way ``createSparseLens`` makes one and get-or-create, so two pickers reading
one slice share one row. The stale ``dataset: null`` / ``at: []`` keys every COLUMN and GRAPH
entry carried are dropped with them.

Alone in its migration, as a data step is; it changes no schema.
"""

from __future__ import annotations

from django.db import migrations

from core.migrations._sub_lens import sparse_slice_lens_id

#: The JSON columns the picker-bearing layer kinds kept their pickers in at this migration,
#: and the two keys a label layer keeps its own under inside ``label_render``.
PICKER_COLUMNS = ("mesh_color_bys", "mesh_filter_bys", "point_color_bys", "point_filter_bys", "network_color_bys", "network_filter_bys")
LABEL_PICKER_KEYS = ("color_bys", "filter_bys")


def _rewrite(apps, entries: list | None) -> bool:  # noqa: ANN001 - the migration's app registry
    changed = False
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "SPARSE" and entry.get("dataset") is not None and entry.get("at"):
            entry["lens"] = str(sparse_slice_lens_id(apps, int(entry["dataset"]), entry["at"]))
            changed = True
        for stale in ("dataset", "at"):
            if stale in entry:
                entry.pop(stale)
                changed = True
    return changed


def repoint(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    Layer = apps.get_model("core", "Layer")
    for layer in Layer.objects.order_by("pk"):
        changed = False
        for column in PICKER_COLUMNS:
            changed |= _rewrite(apps, getattr(layer, column))
        if layer.label_render:
            for key in LABEL_PICKER_KEYS:
                changed |= _rewrite(apps, layer.label_render.get(key))
        if changed:
            layer.save()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_a_layer_narrows_nothing_outside_its_lens"),
    ]

    operations = [
        migrations.RunPython(repoint, migrations.RunPython.noop),
    ]
