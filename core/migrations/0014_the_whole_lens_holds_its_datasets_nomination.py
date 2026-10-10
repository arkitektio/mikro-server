"""A dataset's nomination moves onto its whole lens: one owner for one fact.

`ArrayDataset.default_scene` and the whole array lens' `Lens.default_scene` held the same fact,
and the fallback a crop or a window reads looked at the container -- which only an array had --
so a window over a table never inherited its whole lens' nomination while a crop of an image
did. Every dataset's nomination is copied onto its whole lens (a lens that already nominates
keeps its own); 0015 drops the column. Database only, and alone in its migration.
"""

from __future__ import annotations

from django.db import migrations


def move(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    """Copy each dataset's nomination onto its whole lens, where the lens has none."""
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    ArrayLens = apps.get_model("core", "ArrayLens")
    Lens = apps.get_model("core", "Lens")
    for dataset_id, scene_id in ArrayDataset.objects.filter(default_scene__isnull=False).values_list("pk", "default_scene_id"):
        whole = ArrayLens.objects.filter(dataset_id=dataset_id, slices=[]).order_by("pk").values_list("pk", "default_scene_id").first()
        if whole is None:
            continue  # a dataset with no whole lens yet: the setup job mints it, and the nomination is lost with the column
        lens_id, own = whole
        if own is None:
            Lens.objects.filter(pk=lens_id).update(default_scene_id=scene_id)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_a_whole_lens_may_nominate_for_itself"),
    ]

    operations = [
        # Nothing to undo: the column goes with 0015.
        migrations.RunPython(move, migrations.RunPython.noop),
    ]
