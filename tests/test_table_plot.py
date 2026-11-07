"""createTablePlot: the "Create plot" button. One call, one transaction, a chart of a table's values.

It makes a chart whose world is the table's space -- whose rows -- and draws
the layer through the table's whole lens or a window of it: points at the values of two or three columns, or the
distribution of one. A table then lists the compositions that draw any lens of it
(`visualizations`), charts and scenes alike.
"""

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests.test_chart_values import MEASUREMENTS
from tests.test_column_options import _table

PLOT = """
mutation Plot($input: CreateTablePlotInput!) {
  createTablePlot(input: $input) {
    id
    name
    worldCoordinateSystem { id }
    layers {
      __typename
      kind
      ... on PointsChartLayer { mark pointsLens: lens { id } xColumn yColumn zColumn }
      ... on DistributionChartLayer { mark distributionLens: lens { id } valueColumn groupColumn bins }
    }
  }
}
"""

TABLE = """
query T($id: ID!) {
  tableDataset(id: $id) {
    fullLens { id }
    coordinateSystem { id }
    visualizations { __typename id }
  }
}
"""

GATE = """
mutation W($input: CreateTableLensInput!) { createTableLens(input: $input) { id } }
"""


async def _plot(ctx: HttpContext, **input: object):
    return await schema.execute(PLOT, context_value=ctx, variable_values={"input": input})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scatter_in_one_call(authenticated_context: HttpContext):
    ctx = authenticated_context
    table_id = await _table(ctx, "nuclei", MEASUREMENTS)

    result = await _plot(ctx, table=table_id, xColumn="area", yColumn="volume", kind="POINTS")
    assert not result.errors, result.errors
    chart = result.data["createTablePlot"]
    assert chart["name"] == "nuclei: area against volume"
    assert chart["worldCoordinateSystem"] is not None
    (layer,) = chart["layers"]
    assert (layer["__typename"], layer["mark"], layer["xColumn"], layer["yColumn"]) == ("PointsChartLayer", "MARKERS", "area", "volume")

    read = await schema.execute(TABLE, context_value=ctx, variable_values={"id": table_id})
    assert not read.errors, read.errors
    table = read.data["tableDataset"]
    assert layer["pointsLens"]["id"] == table["fullLens"]["id"]
    assert chart["worldCoordinateSystem"]["id"] == table["coordinateSystem"]["id"], "the world is whose rows: the table's space"
    assert table["visualizations"] == [{"__typename": "Chart", "id": chart["id"]}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_histogram_per_condition_in_one_call(authenticated_context: HttpContext):
    ctx = authenticated_context
    table_id = await _table(ctx, "nuclei", MEASUREMENTS)

    first = await _plot(ctx, table=table_id, xColumn="area", kind="DISTRIBUTION", groupColumn="condition", bins=30, name="areas")
    assert not first.errors, first.errors
    chart = first.data["createTablePlot"]
    assert chart["name"] == "areas"
    (layer,) = chart["layers"]
    assert (layer["__typename"], layer["mark"], layer["valueColumn"], layer["groupColumn"], layer["bins"]) == ("DistributionChartLayer", "HISTOGRAM", "area", "condition", 30)

    second = await _plot(ctx, table=table_id, xColumn="volume", kind="DISTRIBUTION", mark="BOX")
    assert not second.errors, second.errors
    assert second.data["createTablePlot"]["layers"][0]["distributionLens"]["id"] == layer["distributionLens"]["id"], "one whole lens of the table, however many plots"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plot_of_a_gate(authenticated_context: HttpContext):
    """A window on a measurement is a lens of the table like any other, and a plot draws through it."""
    ctx = authenticated_context
    table_id = await _table(ctx, "nuclei", MEASUREMENTS)
    gate = await schema.execute(GATE, context_value=ctx, variable_values={"input": {"tableDataset": table_id, "windows": [{"axis": "area", "min": 50}]}})
    assert not gate.errors, gate.errors
    gate_id = gate.data["createTableLens"]["id"]

    result = await _plot(ctx, table=table_id, lens=gate_id, xColumn="area", yColumn="volume", zColumn="intensity", kind="POINTS")
    assert not result.errors, result.errors
    (layer,) = result.data["createTablePlot"]["layers"]
    assert (layer["pointsLens"]["id"], layer["zColumn"]) == (gate_id, "intensity")

    read = await schema.execute(TABLE, context_value=ctx, variable_values={"id": table_id})
    assert read.data["tableDataset"]["visualizations"] == [{"__typename": "Chart", "id": result.data["createTablePlot"]["id"]}], "a window of the table draws the table"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_refused_plot_makes_nothing(authenticated_context: HttpContext):
    """One transaction: a layer the chart refuses takes the chart with it."""
    ctx = authenticated_context
    table_id = await _table(ctx, "nuclei", MEASUREMENTS)

    refused = await _plot(ctx, table=table_id, xColumn="area", yColumn="volume", kind="DISTRIBUTION")
    assert refused.errors
    assert "summarises one column" in str(refused.errors[0])

    read = await schema.execute(TABLE, context_value=ctx, variable_values={"id": table_id})
    assert read.data["tableDataset"]["visualizations"] == []
