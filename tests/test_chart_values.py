"""A VALUES chart: a table's rows placed in its world and drawn at the values of their columns.

A scene shows *where*: every screen axis is a world axis and data is placed by edges. A chart
shows *how much*: values as position, and at most one axis drawn from its world. Every chart
has a world -- the domain its layers' rows live in. An AXIS chart draws it (one metric axis); a
VALUES chart does not: its world is a table's space, whose rows it draws, its POINTS and
DISTRIBUTION layers are placed into it and name the columns whose values are their positions.
The columns stay columns -- a table's space is its frame -- and a gate (`area >= 50`) is a
window of a table lens on a numeric column, so it is a lens like any other.
"""

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests.test_column_options import _table

MEASUREMENTS = [
    {"name": "label", "dtype": "BIGINT", "role": "ID"},
    {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer ** 2"},
    {"name": "area_mm", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "millimeter ** 2"},
    {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer ** 3"},
    {"name": "intensity", "dtype": "FLOAT", "role": "ATTRIBUTE"},
    {"name": "condition", "dtype": "VARCHAR", "role": "ATTRIBUTE"},
]

TABLE = """
query T($id: ID!) { tableDataset(id: $id) { fullLens { id } coordinateSystem { id axes { name } } } }
"""

CHART = """
mutation C($input: CreateChartInput!) {
  createChart(input: $input) { id kind axis { name } worldCoordinateSystem { id } descriptors }
}
"""

POINTS = """
mutation P($input: CreatePointsChartLayerInput!) {
  createPointsChartLayer(input: $input) {
    id kind mark xColumn yColumn zColumn xUnit yUnit
    asAffine { inputAxes outputAxes total } placement
    windowFilters { column min max }
  }
}
"""

DISTRIBUTION = """
mutation D($input: CreateDistributionChartLayerInput!) {
  createDistributionChartLayer(input: $input) { id kind mark valueColumn groupColumn bins valueUnit }
}
"""

SERIES = """
mutation S($input: CreateSeriesChartLayerInput!) { createSeriesChartLayer(input: $input) { id } }
"""

GATE = """
mutation W($input: CreateTableLensInput!) { createTableLens(input: $input) { id spec windows { axis min max } } }
"""


async def _ok(ctx: HttpContext, document: str, variables: dict) -> dict:
    result = await schema.execute(document, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    return result.data


async def _refused(ctx: HttpContext, document: str, variables: dict) -> str:
    result = await schema.execute(document, context_value=ctx, variable_values=variables)
    assert result.errors, result.data
    return str(result.errors[0])


async def _nuclei(ctx: HttpContext, name: str = "nuclei") -> tuple[str, str, str]:
    table_id = await _table(ctx, name, MEASUREMENTS)
    table = (await _ok(ctx, TABLE, {"id": table_id}))["tableDataset"]
    assert [axis["name"] for axis in table["coordinateSystem"]["axes"]] == ["object"], "a table's space is its frame: its measurements stay columns"
    return table_id, table["fullLens"]["id"], table["coordinateSystem"]["id"]


async def _values_chart(ctx: HttpContext, world: str) -> dict:
    return (await _ok(ctx, CHART, {"input": {"name": "morphology", "kind": "VALUES", "coordinateSystem": world}}))["createChart"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_values_chart_has_its_rows_for_a_world_and_draws_no_axis_of_it(authenticated_context: HttpContext):
    _, _, space = await _nuclei(authenticated_context)
    chart = await _values_chart(authenticated_context, space)
    assert chart["kind"] == "VALUES"
    assert chart["worldCoordinateSystem"]["id"] == space and chart["axis"] is None
    assert chart["descriptors"]["@mikro/axis_type"] == "VALUE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scatter_draws_rows_at_their_values(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)

    layer = (await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area", "yColumn": "volume"}}))["createPointsChartLayer"]
    assert (layer["kind"], layer["mark"]) == ("POINTS", "MARKERS")
    assert (layer["xColumn"], layer["yColumn"], layer["zColumn"]) == ("area", "volume", None)
    assert (layer["xUnit"], layer["yUnit"]) == ("micrometer ** 2", "micrometer ** 3")
    assert layer["placement"] == "PLACED", "the rows are placed in the world: they are this world's rows"
    assert layer["asAffine"] == {"inputAxes": ["object"], "outputAxes": ["object"], "total": True}, "row i is row i"

    depth = (await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area_mm", "yColumn": "volume", "zColumn": "intensity"}}))["createPointsChartLayer"]
    assert depth["zColumn"] == "intensity", "a third column is depth; mm² converts to the µm² already on x"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_layers_in_other_units_are_drawn_beside_each_other(authenticated_context: HttpContext):
    """Neither units nor kinds need agree: a chart lays a scatter beside a histogram as panels (its layout) and overlays only the layers that read their axes alike."""
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area", "yColumn": "volume"}})
    flipped = (await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "volume", "yColumn": "area"}}))["createPointsChartLayer"]
    assert (flipped["xUnit"], flipped["yUnit"]) == ("micrometer ** 3", "micrometer ** 2")

    histogram = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area"}}))["createDistributionChartLayer"]
    assert histogram["kind"] == "DISTRIBUTION", "a histogram beside the scatters, of the same rows"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bound_columns_are_checked(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)

    assert "no column 'perimeter'" in await _refused(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "perimeter", "yColumn": "area"}})
    assert "not numbers" in await _refused(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "condition", "yColumn": "area"}})
    assert "different column" in await _refused(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area", "yColumn": "area"}})
    assert "POINTS" in await _refused(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area", "yColumn": "volume", "mark": "HISTOGRAM"}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_distribution_per_condition(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)

    histogram = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area", "groupColumn": "condition", "bins": 20}}))["createDistributionChartLayer"]
    assert {key: histogram[key] for key in ("kind", "mark", "valueColumn", "groupColumn", "bins", "valueUnit")} == {
        "kind": "DISTRIBUTION",
        "mark": "HISTOGRAM",
        "valueColumn": "area",
        "groupColumn": "condition",
        "bins": 20,
        "valueUnit": "micrometer ** 2",
    }
    box = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area_mm", "mark": "BOX"}}))["createDistributionChartLayer"]
    assert box["mark"] == "BOX" and box["groupColumn"] is None

    assert "measurement has no values to count" in await _refused(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area", "groupColumn": "volume"}})
    assert "bins" in await _refused(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area", "mark": "BOX", "bins": 5}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_placed_and_value_layers_do_not_mix(authenticated_context: HttpContext):
    """A position along a world axis and a measurement are not one kind of thing, even in one unit."""
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    values = await _values_chart(ctx, space)
    assert "VALUES chart" in await _refused(ctx, SERIES, {"input": {"chart": values["id"], "lens": lens}})

    placed = (await _ok(ctx, CHART, {"input": {"name": "over time", "axis": {"name": "t", "type": "TIME", "unit": "second"}}}))["createChart"]
    assert placed["kind"] == "AXIS"
    assert placed["axis"]["name"] == "t"
    assert "AXIS chart" in await _refused(ctx, POINTS, {"input": {"chart": placed["id"], "lens": lens, "xColumn": "area", "yColumn": "volume"}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_gate_is_a_window_on_a_measurement(authenticated_context: HttpContext):
    """`area >= 50` is a lens of the table like any window: drawn through, kept by its spec, a row filter on read."""
    ctx = authenticated_context
    table_id, _, space = await _nuclei(ctx)
    gate = (await _ok(ctx, GATE, {"input": {"tableDataset": table_id, "windows": [{"axis": "area", "min": 50}]}}))["createTableLens"]
    assert gate["windows"] == [{"axis": "area", "min": 50.0, "max": None}]
    assert "RECORDS" in gate["spec"], "a gate selects rows; it pins no coordinate"

    chart = await _values_chart(ctx, space)
    layer = (await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": gate["id"], "xColumn": "area", "yColumn": "volume"}}))["createPointsChartLayer"]
    assert layer["windowFilters"] == [{"column": "area", "min": 50.0, "max": None}]

    again = (await _ok(ctx, GATE, {"input": {"tableDataset": table_id, "windows": [{"axis": "area", "min": 50.0}]}}))["createTableLens"]
    assert again["id"] == gate["id"], "one row per selection"
    assert "does not have" in await _refused(ctx, GATE, {"input": {"tableDataset": table_id, "windows": [{"axis": "condition", "min": 0}]}}), "a text column has no bounds"


AUTO = """
mutation Auto($input: AutoVisualizeInput!) {
  autoVisualize(input: $input) {
    __typename
    ... on Chart { kind worldCoordinateSystem { id } layers { __typename ... on PointsChartLayer { xColumn yColumn } } }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_with_no_place_is_shown_as_a_chart_of_its_values(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    shown = (await _ok(ctx, AUTO, {"input": {"lens": lens}}))["autoVisualize"]
    assert shown == {"__typename": "Chart", "kind": "VALUES", "worldCoordinateSystem": {"id": space}, "layers": [{"__typename": "PointsChartLayer", "xColumn": "area", "yColumn": "area_mm"}]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_values_world_says_whose_rows(authenticated_context: HttpContext):
    """Another table's rows are not this world's rows: nothing places them here, so they are refused."""
    ctx = authenticated_context
    _, _, space = await _nuclei(ctx)
    _, other_lens, _ = await _nuclei(ctx, "organoids")
    chart = await _values_chart(ctx, space)
    message = await _refused(ctx, POINTS, {"input": {"chart": chart["id"], "lens": other_lens, "xColumn": "area", "yColumn": "volume"}})
    assert "whose rows" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_derived_table_draws_in_its_sources_world(authenticated_context: HttpContext):
    """A PCA written as a table derived from the measurements, row for row: its rows are placed in their world, so both draw in one chart."""
    ctx = authenticated_context
    table_id, lens, space = await _nuclei(ctx)
    pca_id = await _table(
        ctx,
        "nuclei pca",
        [{"name": "label", "dtype": "BIGINT", "role": "ID"}, {"name": "pc1", "dtype": "DOUBLE", "role": "ATTRIBUTE"}, {"name": "pc2", "dtype": "DOUBLE", "role": "ATTRIBUTE"}],
        derivedFrom=[{"kind": "TABLE_DATASET", "tableDataset": table_id, "transform": {"kind": "IDENTITY"}, "valueRelation": "TRANSFORMED"}],
    )
    pca_lens = (await _ok(ctx, TABLE, {"id": pca_id}))["tableDataset"]["fullLens"]["id"]

    chart = await _values_chart(ctx, space)
    measured = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "intensity"}}))["createDistributionChartLayer"]
    components = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": pca_lens, "valueColumn": "pc1"}}))["createDistributionChartLayer"]
    assert (measured["valueUnit"], components["valueUnit"]) == (None, None), "both unitless, so one axis"
