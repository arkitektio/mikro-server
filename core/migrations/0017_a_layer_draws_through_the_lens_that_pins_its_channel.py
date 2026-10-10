"""Every layer that narrowed its lens by a channel index now draws through a lens pinning that channel.

A sub-array is a selection and a selection is a lens, so ``Layer.intensity_index`` (with its
axis), the RGB ``red/green/blue_index`` triple, and the ``intensity_index`` of a label recipe, a
phasor recipe and every channel or phasor node of a render graph are each replaced by the lens
that keeps exactly that position: INTENSITY, LABEL and PHASOR layers are repointed onto it; an
RGB layer names three of them; a graph node names it by id (or None, when the layer's own lens
already pins one channel). The lenses are made the way ``createLens`` makes them
(``_sub_lens``), get-or-create, so two layers that indexed one channel share one row.

Alone in its migration: the schema steps sit in 0016 (adds) and 0018 (removes), per
``docs/migrations-and-jobs.md``.
"""

from __future__ import annotations

from django.db import migrations

from core.logic import coords
from core.migrations._sub_lens import dataset_geometry, slices_of, sub_lens_id


def repoint(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    Layer = apps.get_model("core", "Layer")
    ArrayLens = apps.get_model("core", "ArrayLens")

    for layer in Layer.objects.filter(kind__in=("intensity", "rgb", "label", "phasor", "image")).order_by("pk"):
        lens = ArrayLens.objects.filter(pk=layer.lens_id).first()
        if lens is None:
            continue
        shape, names, types = dataset_geometry(apps, lens.dataset_id)
        lens_shape = coords.lens_shape(shape, names, slices_of(lens.slices)) if shape else []
        channel_axes = [name for name, kind in zip(names, types, strict=True) if kind == "CHANNEL"]

        def pin(axis: str | None, index: int | None) -> int:
            """The lens that keeps the channel the index meant along the layer's lens -- the lens itself when it already keeps one."""
            axis = axis or (channel_axes[0] if channel_axes else None)
            if axis is None or axis not in names or not lens_shape or lens_shape[names.index(axis)] <= 1:
                return lens.pk
            return sub_lens_id(apps, lens.pk, axis, index or 0)

        if layer.kind == "intensity":
            layer.lens_id = pin(layer.intensity_axis, layer.intensity_index)
        elif layer.kind == "rgb":
            layer.red_lens_id = pin(layer.intensity_axis, 0 if layer.red_index is None else layer.red_index)
            layer.green_lens_id = pin(layer.intensity_axis, 1 if layer.green_index is None else layer.green_index)
            layer.blue_lens_id = pin(layer.intensity_axis, 2 if layer.blue_index is None else layer.blue_index)
        elif layer.kind in ("label", "phasor"):
            column = "label_render" if layer.kind == "label" else "phasor_render"
            render = getattr(layer, column)
            if render is not None:
                layer.lens_id = pin(render.pop("intensity_axis", None), render.pop("intensity_index", 0))
                setattr(layer, column, render)
        elif layer.kind == "image" and layer.render_graph:
            named: set[int] = set()

            def walk(node: dict) -> None:
                if node.get("kind") in ("channel", "phasor"):
                    target = pin(node.pop("intensity_axis", None), node.pop("intensity_index", 0))
                    node["lens"] = None if target == lens.pk else str(target)
                    if target != lens.pk:
                        named.add(target)
                for child in node.get("children") or []:
                    walk(child)

            walk(layer.render_graph["root"])
            layer.save()
            layer.channel_lenses.set(sorted(named))
            continue
        layer.save()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_an_rgb_layer_names_its_three_component_lenses"),
    ]

    operations = [
        migrations.RunPython(repoint, migrations.RunPython.noop),
    ]
