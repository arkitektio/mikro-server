"""Every table written before `stored_spec` existed is given the spec its declared columns denote.

A table's spec is materialized once, at creation, from its columns -- which were always
stored and never edited, so everything this needs is already in the database. The rule is
frozen here as it stood at this migration (``core.logic.coords.specs_for_columns`` is the
live one): one placement member by the count of SPACE coordinate columns, a modifier per
declared fact, and SERIES for one measured coordinate beside a numeric attribute.

Alone in its migration, as a data step is; it changes no schema.
"""

from __future__ import annotations

from django.db import migrations

PLACEMENT = {0: "RECORDS", 1: "POINTS_1D", 2: "POINTS_2D", 3: "POINTS_3D"}
NUMERIC = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "REAL", "DOUBLE"}


def _spec(columns: list[tuple[str, str | None, str, int | None, int | None]]) -> list[str]:
    """The spec of one table's columns, each ``(role, axis_type, dtype, references_id, node_references_id)``."""
    coordinate_types = [axis_type for role, axis_type, _, _, _ in columns if role == "COORDINATE"]
    roles = {role for role, _, _, _, _ in columns}
    per_collection: dict[int, int] = {}
    for _, _, _, _, collection in columns:
        if collection is not None:
            per_collection[collection] = per_collection.get(collection, 0) + 1

    spec = [PLACEMENT.get(coordinate_types.count("SPACE"), "POINTS_ND")]
    if "TIME" in coordinate_types:
        spec.append("TIMED")
    if "TRACK_ID" in roles:
        spec.append("TRACKED")
    if "GROUP_ID" in roles:
        spec.append("GROUPED")
    if any(references is not None for _, _, _, references, _ in columns):
        spec.append("KEYED")
    if 1 in per_collection.values():
        spec.append("NODE_TABLE")
    if 2 in per_collection.values():
        spec.append("EDGE_TABLE")
    measured = sum(1 for axis_type in coordinate_types if axis_type in ("SPACE", "TIME"))
    has_value = any(role == "ATTRIBUTE" and (dtype.upper() in NUMERIC or dtype.upper().startswith("DECIMAL")) for role, _, dtype, _, _ in columns)
    if measured == 1 and has_value:
        spec.append("SERIES")
    return spec


def materialize(apps, schema_editor) -> None:  # noqa: ANN001 - the migration's app registry and editor
    TableDataset = apps.get_model("core", "TableDataset")
    Column = apps.get_model("core", "Column")

    columns: dict[int, list[tuple[str, str | None, str, int | None, int | None]]] = {}
    for table_id, role, axis_type, dtype, references, node_references in Column.objects.order_by("table_id", "order").values_list(
        "table_id", "role", "axis_type", "dtype", "references_id", "node_references_id"
    ):
        columns.setdefault(table_id, []).append((str(role), None if axis_type is None else str(axis_type), dtype, references, node_references))

    for table_id in TableDataset.objects.order_by("pk").values_list("pk", flat=True):
        TableDataset.objects.filter(pk=table_id).update(stored_spec=_spec(columns.get(table_id, [])))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_a_table_has_a_spec"),
    ]

    operations = [
        migrations.RunPython(materialize, migrations.RunPython.noop),
    ]
