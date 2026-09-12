"""The stored pickers, and what they name.

:mod:`core.logic.column_options` answers what a picker *may* offer; this answers what the
pickers already written down *do* offer, which is the question a delete has to ask. The two are
deliberately separate: the options walker reasons about the coordinate graph and the table
schemas, while this one knows where the entries physically sit -- which JSON column, under which
key -- and that is storage knowledge the walker has no business carrying.

**Why a table cannot simply be deleted out from under a picker.** A picker entry names its table
by id, in JSON, so there is no foreign key and nothing cascades. Delete the table and the entry
survives as a join nothing can execute: the layer still claims to colour by a column of a table
that is not there, and the failure surfaces at render time, in a viewer, to whoever opens the
scene next -- which is exactly the failure the mutation boundary refuses an unreachable table to
prevent. Refusing the delete puts the discovery back where the decision is being made.

That refusal is PROTECT in the ordinary Django sense, and it is spelled out here rather than
declared on a field because the reference is not a field: it is a value inside a JSON document,
and `on_delete` has nothing to attach to.
"""

from typing import TYPE_CHECKING

from django.db.models import Q

from core import enums, models
from core.logic import attribute_plans as attribute_plans_logic

if TYPE_CHECKING:
    from django.db.models import QuerySet


#: The JSON columns the picker-bearing layer kinds keep their pickers in, and the two keys a
#: label layer keeps its own under inside ``label_render``. Listed rather than derived because
#: they *are* the storage shape: a new picker on a new layer kind must be added here, and
#: ``tests/test_architecture.py::test_every_picker_column_is_guarded`` is what will say so.
_PICKER_COLUMNS = (
    "mesh_color_bys",
    "mesh_filter_bys",
    "point_color_bys",
    "point_filter_bys",
    "network_color_bys",
    "network_filter_bys",
)
_LABEL_PICKER_KEYS = ("color_bys", "filter_bys")


def _names_table(table_id: str) -> Q:
    """Every way a stored picker entry can name one table.

    Two ways per picker, and the second is the one that is easy to forget: an entry names its
    terminal table in ``table``, *and* every table its ``join_path`` hops through. Deleting a
    table a path merely passes through breaks the join exactly as thoroughly as deleting the one
    the value is read from.

    Expressed as JSONB containment (``@>``), which is structural: the pattern matches when some
    element of the list contains it, at any depth the pattern itself describes.
    """
    patterns = ([{"table": table_id}], [{"join_path": [{"table": table_id}]}])

    query = Q()
    for pattern in patterns:
        for column in _PICKER_COLUMNS:
            query |= Q(**{f"{column}__contains": pattern})
        for key in _LABEL_PICKER_KEYS:
            query |= Q(**{f"label_render__{key}__contains": pattern})
    return query


def _names_table_any(table_ids: "set[str]") -> Q:
    """The union of :func:`_names_table` over several tables, for one query instead of N."""
    query = Q()
    for table_id in table_ids:
        query |= _names_table(table_id)
    return query


def layers_naming_table(table) -> "QuerySet[models.Layer]":
    """The layers whose colour or filter picker names this table, by any route.

    Not organization-scoped, deliberately. A cross-org layer naming this table should not exist,
    but a guard that only looks at the caller's own organization would let a delete break
    something it could not see -- and the failure mode of looking too widely is refusing a
    delete, which is recoverable.
    """
    return models.Layer.objects.filter(_names_table(str(table.pk))).select_related("scene").order_by("pk")


def assert_table_not_in_a_picker(table) -> None:
    """Refuse to delete a table some layer still colours or filters by.

    The PROTECT half of ``deleteTableDataset``. Named layers and scenes in the refusal, because
    "something references this" without saying what is a dead end for whoever hit it.
    """
    layers = list(layers_naming_table(table)[:5])
    if not layers:
        return

    total = layers_naming_table(table).count()
    described = ", ".join(f"layer {layer.pk} in scene '{layer.scene.name}'" for layer in layers)
    more = f" (and {total - len(layers)} more)" if total > len(layers) else ""
    raise ValueError(
        f"Table dataset '{table.name}' ({table.pk}) cannot be deleted: {total} layer(s) colour or filter by a column of it -- {described}{more}. "
        "A picker naming a deleted table is a join nothing can execute, and it would look valid until a renderer tried it. "
        "Clear those entries first (pass the picker without them, or `[]` to remove it), or delete the layers."
    )


def _names_sparse_dataset(dataset_id: str) -> Q:
    """Every way a stored picker entry can name one sparse dataset.

    One way, not two: a sparse colouring names its dataset in ``dataset`` and has no join path,
    because there are no hops -- the position is a row of the table its axis references, and
    that table is reached by ``references`` rather than by anything stored here.

    A separate function from :func:`_names_table` rather than a branch inside it, because the
    two never overlap: an entry names a table *or* a dataset, and the discriminator says which.
    """
    query = Q()
    for column in _PICKER_COLUMNS:
        query |= Q(**{f"{column}__contains": [{"dataset": dataset_id}]})
    for key in _LABEL_PICKER_KEYS:
        query |= Q(**{f"label_render__{key}__contains": [{"dataset": dataset_id}]})
    return query


def layers_naming_sparse_dataset(dataset) -> "QuerySet[models.Layer]":
    """Every layer whose pickers name this sparse dataset."""
    return models.Layer.objects.filter(_names_sparse_dataset(str(dataset.pk))).select_related("scene").order_by("pk")


def assert_sparse_dataset_not_in_a_picker(dataset) -> None:
    """Refuse to delete a sparse dataset some layer still colours by.

    The PROTECT half of ``deleteSparseDataset``, and the reason this module's key lists are
    "listed rather than derived because they *are* the storage shape": a colouring names its
    source by id inside JSON, so there is no foreign key to cascade, and a deleted source leaves
    an entry that looks valid until a renderer reaches for bytes that are gone.
    """
    layers = list(layers_naming_sparse_dataset(dataset)[:5])
    if not layers:
        return

    total = layers_naming_sparse_dataset(dataset).count()
    described = ", ".join(f"layer {layer.pk} in scene '{layer.scene.name}'" for layer in layers)
    more = f" (and {total - len(layers)} more)" if total > len(layers) else ""
    raise ValueError(
        f"Sparse dataset '{dataset.name}' ({dataset.pk}) cannot be deleted: {total} layer(s) colour by a slice of it -- {described}{more}. "
        "A picker naming a deleted matrix would look valid until a renderer went looking for the bytes. "
        "Clear those entries first (pass the picker without them, or `[]` to remove it), or delete the layers."
    )


def _picker_tables(layer) -> "set[str]":
    """Every table id this layer's two pickers name, terminal tables and hop tables alike."""
    entries: list = []
    for column in _PICKER_COLUMNS:
        entries += list(getattr(layer, column, None) or [])
    render = layer.label_render or {}
    for key in _LABEL_PICKER_KEYS:
        entries += list(render.get(key) or [])

    tables: set[str] = set()
    for entry in entries:
        tables.add(str(entry.get("table")))
        for step in entry.get("join_path") or []:
            tables.add(str(step.get("table")))
    tables.discard("None")
    return tables


def _layer_source_system(layer):
    """The space this layer's ids dereference from, or None for a kind that publishes no picker.

    A point layer's answer is its table's own system -- and may honestly be None, since a
    table with no coordinate system still seeds *itself* into its picker's reachable set. The
    guard then skips the re-walk, which fails safe: the entry over the layer's own table needs
    no edge to survive, and one over a further table stays unguarded rather than wrongly
    refused.
    """
    from core.logic import column_options as column_options_logic

    if layer.mesh_collection_id is not None:
        return column_options_logic.mesh_collection_system(layer.mesh_collection)
    if layer.network_collection_id is not None:
        return column_options_logic.network_collection_system(layer.network_collection)
    if layer.table_dataset_id is not None:
        return layer.table_dataset.coordinate_system_or_none
    if layer.lens_id is not None:
        return column_options_logic.lens_source_system(layer.lens)
    return None


def assert_edge_not_stranding_a_picker(edge) -> None:
    """Refuse to delete a FIELD edge that some layer's picker is reaching a table through.

    The other way to arrive at a picker naming an unreachable table: leave the table in place
    and remove the crossing. `deleteTableDataset` is refused for the first route, and this is
    the second.

    Asked as a **hypothetical**, not guessed from the edge: the walk is re-run without this edge
    and the answer compared against what the picker names. Guessing would be wrong the moment a
    rival edge still provides the crossing -- RFC-9 allows rivals, and refusing a delete that
    breaks nothing is its own kind of wrong.

    Only FIELD edges. A derivation or registration edge upstream can strand a picker too, by
    disconnecting the fact component the walk crosses; catching that would mean re-walking for
    every layer with a picker on every edge delete, and it is not what this guard claims to do.
    """
    if edge.kind != enums.TransformKindChoices.FIELD.value:
        return

    output = getattr(edge, "output", None)
    tables = {str(table.pk) for table in output.table_datasets.all()} if output is not None else set()
    if not tables:
        return

    stranded: list[tuple] = []
    for layer in models.Layer.objects.filter(_names_table_any(tables)).select_related("scene", "lens", "mesh_collection", "network_collection", "table_dataset").order_by("pk"):
        named = _picker_tables(layer) & tables
        if not named:
            continue
        system = _layer_source_system(layer)
        if system is None:
            continue
        still = set(attribute_plans_logic.field_reachable_tables(system, layer.scene.organization, excluding=[edge.pk]))
        lost = named - still
        if lost:
            stranded.append((layer, sorted(lost)))

    if not stranded:
        return

    described = ", ".join(f"layer {layer.pk} in scene '{layer.scene.name}' (table {', '.join(lost)})" for layer, lost in stranded[:5])
    raise ValueError(
        f"Transformation {edge.pk} cannot be deleted: it is the FIELD edge {len(stranded)} layer(s) reach a picker's table through -- {described}. "
        "Removing it would leave those entries naming a table nothing dereferences into, which is a join no renderer can execute. "
        "Clear the entries first, or delete the layers."
    )
