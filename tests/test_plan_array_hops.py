"""A plan's chain can end in a dense array: mask pixel -> row id -> that object's row of an array.

A table of cells is keyed by a mask, so probing the mask lands in the table. A second dataset
holds one trace per cell, ``(cell, t)``, and says so the only way the graph can: it is derived
from the table by a BY_DIMENSION edge that maps its ``cell`` axis onto the table's ``cell_id``.
That edge is the same statement a matrix axis reference makes -- positions along this axis are
rows of that table -- so the walk follows it, and the plan says which position an id is.

These go through the real mutations and the real ``attributePlans`` query. Nothing here reads a
store: a plan is a recipe.
"""

import uuid
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import join_walk
from mikro_server.schema import schema
from tests import seed

PLANS = """
query Plans($system: ID!, $maxJoinDepth: Int) {
  attributePlans(system: $system, maxJoinDepth: $maxJoinDepth) {
    hops {
      index parent cardinality
      via { column { name } axis }
      table { name }
      sparseDataset { name }
      arrayDataset { id name dataArrays { level store { id } } }
      joinPath { column { name } }
      lookup { kind keyAxis keyHeld valueAxes keyMap { scale offset } keyColumns { axis } store { id } sparseArray { path } }
    }
  }
}
"""

YX_AXES = [seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]

CELL_T = [{"name": "cell", "type": "INDEX"}, {"name": "t", "type": "TIME"}]

#: `cell_id = cell + 1`: ids start at one because zero is the mask's background.
ONE_BASED = {"kind": "BY_DIMENSION", "inputAxes": ["cell"], "outputAxes": ["cell_id"], "affine": [[1.0, 1.0]]}


async def _mask(ctx: HttpContext, name: str = "cell labels") -> models.ArrayDataset:
    """A label mask whose level-0 array has a zarr store, so a plan has something to sample."""
    dataset = await seed.create_array_dataset(ctx, name, axes=YX_AXES, shapes=[[64, 64]])

    def attach() -> None:
        store = models.ZarrStore.objects.create(path=f"s3://zarr/{name}", bucket="zarr", key=name.replace(" ", "-"), organization=ctx.request.organization)
        array = dataset.data_arrays.get(level=0)
        array.store = store
        array.save()

    await sync_to_async(attach)()
    return dataset


async def _cells(ctx: HttpContext, mask: models.ArrayDataset, *extra_columns: dict) -> dict:
    """The cell table: one INDEX column the mask's pixels identify, and a measurement."""
    columns = [{"name": "cell_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}, {"name": "peak_dff", "dtype": "DOUBLE", "role": "ATTRIBUTE"}, *extra_columns]
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, "cells", columns, keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}])},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]


async def _derived_array(ctx: HttpContext, table: dict, *, transform: dict | None, name: str = "traces", axes: list[dict] | None = None, shape: list[int] | None = None):
    """An array dataset derived from the table, through the real ingest mutation."""
    shape = shape or [12, 600]
    key = f"{name}-{uuid.uuid4().hex[:8]}"
    store = await sync_to_async(models.ZarrStore.objects.create)(
        path=f"s3://zarr/{key}", bucket="zarr", key=key, shape=shape, chunks=shape, version="3", dtype="float32", populated=True, organization=ctx.request.organization
    )
    entry: dict = {"kind": "TABLE_DATASET", "tableDataset": table["id"], "valueRelation": "TRANSFORMED"}
    if transform is not None:
        entry["transform"] = transform
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        return await schema.execute(
            "mutation D($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={"input": {"name": name, "data": str(store.id), "scales": [], "axes": axes or CELL_T, "derivedFrom": [entry]}},
        )


async def _traces(ctx: HttpContext, table: dict, **kwargs: object) -> str:
    result = await _derived_array(ctx, table, **kwargs)
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]["id"]


async def _plan(ctx: HttpContext, mask: models.ArrayDataset, **variables: object) -> dict:
    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    result = await schema.execute(PLANS, context_value=ctx, variable_values={"system": str(system.pk), **variables})
    assert not result.errors, result.errors
    (plan,) = result.data["attributePlans"]
    return plan


def _array_hops(plan: dict) -> list[dict]:
    return [hop for hop in plan["hops"] if hop["lookup"]["kind"] == "ARRAY"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plan_hops_from_the_cell_table_into_the_traces_derived_from_it(authenticated_context: HttpContext):
    """Mask pixel, then the cell's row, then the cell's trace: three reads, one plan."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    traces = await _traces(authenticated_context, cells, transform=ONE_BASED)

    plan = await _plan(authenticated_context, mask)
    landing, hop = plan["hops"]

    assert landing["lookup"]["kind"] == "TABLE" and landing["table"]["name"] == "cells"
    assert landing["arrayDataset"] is None

    assert hop["index"] == 1 and hop["parent"] == 0
    assert hop["cardinality"] == "ONE", "one row of the table is one position along the array"
    assert hop["table"] is None and hop["sparseDataset"] is None, "exactly one landing is set"
    assert hop["arrayDataset"]["id"] == traces and hop["arrayDataset"]["name"] == "traces"
    assert [(level["level"], level["store"] is not None) for level in hop["arrayDataset"]["dataArrays"]] == [(0, True)], "the levels to read from ride on the dataset"
    assert hop["via"] == {"column": {"name": "cell_id"}, "axis": "cell"}, "the row's id column, bound along the array's axis"
    assert hop["joinPath"] == [], "no picker entry names a chain that leaves the tables"

    lookup = hop["lookup"]
    assert lookup["keyAxis"] == "cell" and lookup["keyHeld"] == "cell_id", "held under the column's name, bound to the array's axis"
    assert lookup["valueAxes"] == ["t"], "what comes back is the whole of every other axis"
    assert lookup["keyMap"] == {"scale": 1.0, "offset": -1.0}, "cell_id = cell + 1, so cell = cell_id - 1"
    assert lookup["store"] is None and lookup["keyColumns"] == [] and lookup["sparseArray"] is None, "the other shapes' fields stay empty"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transform",
    [
        {"kind": "BY_DIMENSION", "inputAxes": ["cell"], "outputAxes": ["cell_id"], "affine": [[2.0, 1.0]]},
        {"kind": "BY_DIMENSION", "inputAxes": ["cell"], "outputAxes": ["cell_id"], "scale": [2.0], "translation": [1.0]},
    ],
    ids=["affine", "scale-and-translation"],
)
async def test_the_key_map_is_the_edge_inverted_however_the_edge_was_spelled(authenticated_context: HttpContext, transform: dict):
    """`cell_id = 2 * cell + 1` is one map with two spellings; the plan states its inverse once."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    await _traces(authenticated_context, cells, transform=transform)

    (hop,) = _array_hops(await _plan(authenticated_context, mask))
    assert hop["lookup"]["keyMap"] == {"scale": 0.5, "offset": -0.5}, "id 5 is position 2; id 4 is no position at all, and the worker can tell"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bare_by_dimension_maps_ids_onto_positions_unchanged(authenticated_context: HttpContext):
    """An edge that names the two axes and states no numbers says they count alike."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    await _traces(authenticated_context, cells, transform={"kind": "BY_DIMENSION", "inputAxes": ["cell"], "outputAxes": ["cell_id"]})

    (hop,) = _array_hops(await _plan(authenticated_context, mask))
    assert hop["lookup"]["keyMap"] == {"scale": 1.0, "offset": 0.0}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_channel_axis_can_carry_the_ids(authenticated_context: HttpContext):
    """One channel per object is the same enumeration under another axis type."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    await _traces(
        authenticated_context,
        cells,
        axes=[{"name": "c", "type": "CHANNEL"}, {"name": "t", "type": "TIME"}],
        transform={"kind": "BY_DIMENSION", "inputAxes": ["c"], "outputAxes": ["cell_id"], "affine": [[1.0, 1.0]]},
    )

    (hop,) = _array_hops(await _plan(authenticated_context, mask))
    assert hop["lookup"]["keyAxis"] == "c" and hop["lookup"]["valueAxes"] == ["t"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_derivation_that_states_no_geometry_is_not_a_hop(authenticated_context: HttpContext):
    """An omitted transform is UNMAPPABLE: lineage is recorded, and nothing says which row is whose."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    await _traces(authenticated_context, cells, transform=None)

    assert _array_hops(await _plan(authenticated_context, mask)) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measuring_axis_is_never_a_key(authenticated_context: HttpContext):
    """A row id is a position along an axis that enumerates. Along space it would be a place."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    made = await _derived_array(
        authenticated_context,
        cells,
        axes=[{"name": "x", "type": "SPACE"}, {"name": "t", "type": "TIME"}],
        transform={"kind": "BY_DIMENSION", "inputAxes": ["x"], "outputAxes": ["cell_id"]},
    )

    assert not made.errors, made.errors
    assert _array_hops(await _plan(authenticated_context, mask)) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_with_nothing_stored_is_not_a_hop(authenticated_context: HttpContext):
    """A hop is a recipe for a read, and there is nothing here to read."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    traces = await _traces(authenticated_context, cells, transform=ONE_BASED)
    await sync_to_async(models.DataArray.objects.filter(dataset_id=traces).update)(store=None)

    assert _array_hops(await _plan(authenticated_context, mask)) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_mask_that_keys_the_table_is_not_an_array_to_hop_into(authenticated_context: HttpContext):
    """The mask reaches the table too, by a FIELD. That is the plan's root, not a row to read."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    traces = await _traces(authenticated_context, cells, transform=ONE_BASED)

    hops = _array_hops(await _plan(authenticated_context, mask))
    assert [hop["arrayDataset"]["id"] for hop in hops] == [traces]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_array_hop_is_bounded_like_any_other_and_nothing_follows_it(authenticated_context: HttpContext):
    """Depth zero is the landing alone. Deeper, the array is still where its branch ends."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    await _traces(authenticated_context, cells, transform=ONE_BASED)

    assert len((await _plan(authenticated_context, mask, maxJoinDepth=0))["hops"]) == 1

    deep = await _plan(authenticated_context, mask, maxJoinDepth=4)
    assert [hop["lookup"]["kind"] for hop in deep["hops"]] == ["TABLE", "ARRAY"]
    assert all(hop["parent"] != 1 for hop in deep["hops"]), "nothing binds from an array's row"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_arrays_derived_from_one_table_are_two_hops_in_a_stable_order(authenticated_context: HttpContext):
    """Traces and spikes of the same cells: both are under the point, and the list does not shuffle."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    first = await _traces(authenticated_context, cells, transform=ONE_BASED, name="traces")
    second = await _traces(authenticated_context, cells, transform=ONE_BASED, name="spikes")

    once = _array_hops(await _plan(authenticated_context, mask))
    again = _array_hops(await _plan(authenticated_context, mask))
    assert [hop["arrayDataset"]["id"] for hop in once] == [first, second]
    assert [(hop["index"], hop["parent"]) for hop in once] == [(1, 0), (2, 0)]
    assert once == again


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_branch_standing_on_the_table_gets_the_array_hop(authenticated_context: HttpContext):
    """Two plans landing in one table -- sibling masks of the same cells -- each reach the traces."""
    mask = await _mask(authenticated_context)
    cells = await _cells(authenticated_context, mask)
    traces = await _traces(authenticated_context, cells, transform=ONE_BASED)
    organization = authenticated_context.request.organization

    def walk() -> dict[int, list[join_walk.JoinHop]]:
        table = models.TableDataset.objects.get(pk=cells["id"])
        return join_walk.walk_joins([join_walk.JoinRoot(table=table), join_walk.JoinRoot(table=table)], organization, max_join_depth=1)

    hops = await sync_to_async(walk)()
    assert [[str(hop.array_dataset.pk) for hop in found] for found in hops.values()] == [[traces], [traces]]
