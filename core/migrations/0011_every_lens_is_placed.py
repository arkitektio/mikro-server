"""The data half before 0012: every lens stands in a coordinate system.

A whole lens without one -- written before every lens had a system -- is placed in its
container's, exactly as `merge_duplicate_lenses` has placed such rows since the lens tables
were split. Anything else still without a system (a sliced array lens, which `createLens` has
minted a space for since the beginning, or a windowed lens whose container has no space) was
written by hand: it is named and the migration stops, because minting a crop's space here
would be a second, untested copy of `create_lens`. Give the row a system or delete it, then
migrate again. On a hub that 0004 and the setup job have visited, neither branch has work.

Alone in its migration: 0012 alters the column, and Postgres will not alter a table in the
transaction that rewrote its rows.
"""

from __future__ import annotations

from django.db import migrations

#: (lens model, container model, container FK) per kind.
KINDS = [
    ("ArrayLens", "ArrayDataset", "dataset"),
    ("TableLens", "TableDataset", "table_dataset"),
    ("SparseLens", "SparseDataset", "sparse_dataset"),
    ("MeshLens", "MeshCollection", "mesh_collection"),
    ("NetworkLens", "NetworkCollection", "network_collection"),
    ("AnnotationLens", "AnnotationCollection", "annotation_collection"),
]


def place(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    """Place every whole lens without a system in its container's; name anything else that has none."""
    Lens = apps.get_model("core", "Lens")
    unplaceable: list[str] = []
    for kind_name, container_name, field in KINDS:
        Kind = apps.get_model("core", kind_name)
        Container = apps.get_model("core", container_name)
        selection = "slices" if kind_name == "ArrayLens" else "windows"
        for row in Kind.objects.filter(coordinate_system__isnull=True).values("pk", f"{field}_id", selection):
            system_id = Container.objects.filter(pk=row[f"{field}_id"]).values_list("coordinate_system_id", flat=True).first()
            if not row[selection] and system_id is not None:
                Lens.objects.filter(pk=row["pk"]).update(coordinate_system_id=system_id)
            else:
                why = "it selects something, so it needs a space of its own" if row[selection] else f"its {container_name} {row[f'{field}_id']} has no coordinate system"
                unplaceable.append(f"lens {row['pk']} ({kind_name}): {why}")
    if unplaceable:
        raise RuntimeError("Every lens has to stand in a coordinate system before 0012 makes the column NOT NULL, and these cannot be placed from the database alone -- give each a system (createLens mints a crop's) or delete it, then migrate again: " + "; ".join(unplaceable))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_every_lens_says_who_made_it"),
    ]

    operations = [
        # Nothing to undo: a placed whole lens is where it always was in meaning.
        migrations.RunPython(place, migrations.RunPython.noop),
    ]
