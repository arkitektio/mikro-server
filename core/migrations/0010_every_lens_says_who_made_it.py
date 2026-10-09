"""The data half of 0009: every lens that exists is stamped with who made it and when.

A sliced array lens was made in one transaction with its own coordinate system, and that row
kept the creator and the time; it is the record of the crop. A whole lens is its container's
handle and carries the container's provenance -- creator, task, and the container's creation
time, which is when the whole lens came to exist in meaning if not in rows. A windowed lens
over a table, a sparse dataset, a mesh, a network or an annotation collection is stamped the
same way: nothing else recorded who asked for it, and its container's facts are the honest
ones there are. A mesh or network collection carries no task.

Database only, so a migration rather than a job, and alone in its own: Postgres will not alter
a table in the transaction that rewrote its rows.
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


def stamp(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    """Stamp every lens row: a sliced array lens from its own system, the rest from the container."""
    Lens = apps.get_model("core", "Lens")
    CoordinateSystem = apps.get_model("core", "CoordinateSystem")
    for kind_name, container_name, field in KINDS:
        Kind = apps.get_model("core", kind_name)
        Container = apps.get_model("core", container_name)
        container_field_names = {f.name for f in Container._meta.get_fields()}
        for row in Kind.objects.all().values("pk", f"{field}_id", "coordinate_system_id", *(("slices",) if kind_name == "ArrayLens" else ())).iterator():
            container = Container.objects.filter(pk=row[f"{field}_id"]).values("creator_id", "created_at", *[name for name in ("created_through_id", "created_through_by_id") if name.removesuffix("_id") in container_field_names]).first()
            if container is None:
                continue
            sliced = kind_name == "ArrayLens" and bool(row["slices"])
            stamped = {
                "creator_id": container["creator_id"],
                "created_at": container["created_at"],
                "created_through_id": container.get("created_through_id"),
                "created_through_by_id": container.get("created_through_by_id"),
            }
            if sliced and row["coordinate_system_id"] is not None:
                own = CoordinateSystem.objects.filter(pk=row["coordinate_system_id"]).values("creator_id", "created_at").first()
                if own is not None:
                    # The crop's own space was written in the same transaction as the crop, by
                    # whoever asked for it; the task is not recorded there.
                    stamped.update(creator_id=own["creator_id"], created_at=own["created_at"], created_through_id=None, created_through_by_id=None)
            Lens.objects.filter(pk=row["pk"]).update(**stamped)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_a_lens_has_a_creator_a_time_and_a_task"),
    ]

    operations = [
        # Nothing to undo: the columns go with 0009.
        migrations.RunPython(stamp, migrations.RunPython.noop),
    ]
