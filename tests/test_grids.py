"""A grid shows *which*: the members of one or two enumerating axes, a value as each cell's colour.

A scene shows where (screen axes a place), a chart how much (values as position). A grid's rows
and columns are members -- genes and cells, objects and channels -- in order, with no distance
between them. It adopts its world (a sparse dataset's space, a long-form table's) and every layer
is placed into it and covers its axes: a HEATMAP reads an array or a sparse matrix, CELLS read a
table's rows coloured by a column.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_column_options import _table
from tests.test_sparse_datasets import _expression, _mask

GRID = """
mutation G($input: CreateGridInput!) {
  createGrid(input: $input) { id rowAxis { name type } columnAxis { name type } worldCoordinateSystem { id } descriptors }
}
"""

HEATMAP = """
mutation H($input: CreateHeatmapGridLayerInput!) {
  createHeatmapGridLayer(input: $input) { id kind colormap placement asAffine { inputAxes outputAxes total } }
}
"""

CELLS = """
mutation C($input: CreateCellsGridLayerInput!) {
  createCellsGridLayer(input: $input) { id kind valueColumn valueUnit placement tableDataset { id } }
}
"""

TABLE = """
query T($id: ID!) { tableDataset(id: $id) { fullLens { id } coordinateSystem { id } visualizations { __typename id } } }
"""

SPARSE = """
query S($id: ID!) { sparseDataset(id: $id) { fullLens { id } coordinateSystem { id } } }
"""

CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) { createCoordinateSystem(input: $input) { id } }
"""

DELETE_LENS = """
mutation D($input: DeleteLensInput!) { deleteLens(input: $input) }
"""

LONG_FORM = [
    {"name": "cell", "dtype": "BIGINT", "axisType": "INDEX"},
    {"name": "c", "dtype": "BIGINT", "axisType": "CHANNEL"},
    {"name": "mean", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "a.u.", "variesOver": ["c"]},
    {"name": "label", "dtype": "VARCHAR", "role": "LABEL"},
]


async def _ok(ctx: HttpContext, document: str, variables: dict) -> dict:
    result = await schema.execute(document, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    return result.data


async def _refused(ctx: HttpContext, document: str, variables: dict) -> str:
    result = await schema.execute(document, context_value=ctx, variable_values=variables)
    assert result.errors, result.data
    return str(result.errors[0])


async def _long_form(ctx: HttpContext, name: str = "per channel") -> dict:
    table_id = await _table(ctx, name, LONG_FORM)
    return {"id": table_id, **(await _ok(ctx, TABLE, {"id": table_id}))["tableDataset"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_long_form_table_is_a_grid_of_cells_against_channels(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await _long_form(ctx)
    grid = (await _ok(ctx, GRID, {"input": {"name": "means", "coordinateSystem": table["coordinateSystem"]["id"]}}))["createGrid"]
    assert (grid["rowAxis"], grid["columnAxis"]) == ({"name": "cell", "type": "INDEX"}, {"name": "c", "type": "CHANNEL"})
    assert grid["descriptors"] == {**grid["descriptors"], "@mikro/row_axis_type": "INDEX", "@mikro/column_axis_type": "CHANNEL"}

    cells = (await _ok(ctx, CELLS, {"input": {"grid": grid["id"], "lens": table["fullLens"]["id"]}}))["createCellsGridLayer"]
    assert (cells["kind"], cells["valueColumn"], cells["valueUnit"], cells["placement"]) == ("CELLS", "mean", "a.u.", "PLACED"), "the one numeric column, inferred"

    shown = (await _ok(ctx, TABLE, {"id": table["id"]}))["tableDataset"]["visualizations"]
    assert shown == [{"__typename": "Grid", "id": grid["id"]}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_matrix_is_a_heatmap(authenticated_context: HttpContext):
    ctx = authenticated_context
    mask = await _mask(ctx)
    dataset_id = await _expression(ctx, mask)
    sparse = (await _ok(ctx, SPARSE, {"id": dataset_id}))["sparseDataset"]

    grid = (await _ok(ctx, GRID, {"input": {"name": "expression", "coordinateSystem": sparse["coordinateSystem"]["id"]}}))["createGrid"]
    heatmap = (await _ok(ctx, HEATMAP, {"input": {"grid": grid["id"], "lens": sparse["fullLens"]["id"], "colormap": "MAGMA"}}))["createHeatmapGridLayer"]
    assert (heatmap["kind"], heatmap["colormap"], heatmap["placement"]) == ("HEATMAP", "MAGMA", "PLACED")
    assert heatmap["asAffine"]["total"] is True, "every position is a cell"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_grid_lays_out_members(authenticated_context: HttpContext):
    """A measured axis is a scene's or a chart's; three axes are more than rows and columns."""
    ctx = authenticated_context
    stage = (await _ok(ctx, CREATE_CS, {"input": {"name": "stage", "axes": [{"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]}}))["createCoordinateSystem"]["id"]
    message = await _refused(ctx, GRID, {"input": {"name": "g", "coordinateSystem": stage}})
    assert "measured axis" in message and "scene" in message

    cube = (await _ok(ctx, CREATE_CS, {"input": {"name": "cube", "axes": [{"name": name, "type": "INDEX", "unit": "a.u."} for name in ("a", "b", "c")]}}))["createCoordinateSystem"]["id"]
    assert "one or two" in await _refused(ctx, GRID, {"input": {"name": "g", "coordinateSystem": cube}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_each_kind_reads_its_own(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await _long_form(ctx)
    grid = (await _ok(ctx, GRID, {"input": {"name": "means", "coordinateSystem": table["coordinateSystem"]["id"]}}))["createGrid"]

    assert "drawn as CELLS" in await _refused(ctx, HEATMAP, {"input": {"grid": grid["id"], "lens": table["fullLens"]["id"]}})
    assert "not a numeric attribute" in await _refused(ctx, CELLS, {"input": {"grid": grid["id"], "lens": table["fullLens"]["id"], "valueColumn": "label"}})

    other = await _long_form(ctx, "other table")
    assert "the world of grid" in await _refused(ctx, CELLS, {"input": {"grid": grid["id"], "lens": other["fullLens"]["id"]}}), "another table's rows are not this world's members"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_a_grid_draws_through_is_kept(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await _long_form(ctx)
    gate = await seed.create_windowed_lens(ctx, await models.TableDataset.objects.aget(pk=table["id"]), [{"axis": "cell", "min": 0, "max": 10}])
    grid = (await _ok(ctx, GRID, {"input": {"name": "means", "coordinateSystem": table["coordinateSystem"]["id"]}}))["createGrid"]
    await _ok(ctx, CELLS, {"input": {"grid": grid["id"], "lens": str(gate.pk)}})

    message = await _refused(ctx, DELETE_LENS, {"input": {"id": str(gate.pk)}})
    assert "grid layer" in message and "'means'" in message
    assert await sync_to_async(models.Lens.objects.filter(pk=gate.pk).exists)()


AUTO = """
mutation Auto($input: AutoVisualizeInput!) {
  autoVisualize(input: $input) { __typename ... on Grid { layers { __typename ... on CellsGridLayer { valueColumn } } } }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_long_form_table_lens_is_shown_as_a_grid(authenticated_context: HttpContext):
    ctx = authenticated_context
    table = await _long_form(ctx)
    shown = (await _ok(ctx, AUTO, {"input": {"lens": table["fullLens"]["id"]}}))["autoVisualize"]
    assert shown == {"__typename": "Grid", "layers": [{"__typename": "CellsGridLayer", "valueColumn": "mean"}]}
