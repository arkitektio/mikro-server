"""Chart layers are retuned as scene layers are: one update per kind, for what that kind reads.

`updateChartLayer` restyles any layer. What a layer READS -- its lens and its columns -- is its
kind's own update: `updateSeriesChartLayer` (a value column), `updatePointsChartLayer` (x, y, z),
`updateDistributionChartLayer` (value, grouping, bins, histogram or box) and
`updateTraceChartLayer` (another array lens). Each holds the merged state to the rules creating
the layer does, refuses another kind's layer by naming the update it takes, and re-places a
lens only when it changes.
"""

import pytest
from kante.context import HttpContext

from core import models
from tests import seed
from tests.test_chart_values import DISTRIBUTION, GATE, POINTS, _nuclei, _ok, _refused, _values_chart
from tests.test_charts import _MEASUREMENTS, SERIES, _chart, _register
from tests.test_charts import _ok as _ok_input
from tests.test_charts import _table as _series_table

UPDATE_POINTS = """
mutation U($input: UpdatePointsChartLayerInput!) {
  updatePointsChartLayer(input: $input) { id mark markerSize xColumn yColumn zColumn xUnit yUnit tableDataset { id } }
}
"""

UPDATE_DISTRIBUTION = """
mutation U($input: UpdateDistributionChartLayerInput!) {
  updateDistributionChartLayer(input: $input) { id mark valueColumn groupColumn bins valueUnit }
}
"""

UPDATE_SERIES = """
mutation U($input: UpdateSeriesChartLayerInput!) { updateSeriesChartLayer(input: $input) { id valueColumn valueUnit } }
"""

UPDATE_TRACE = """
mutation U($input: UpdateTraceChartLayerInput!) { updateTraceChartLayer(input: $input) { id } }
"""


async def _scatter(ctx: HttpContext) -> tuple[dict, dict, str]:
    """A VALUES chart over a table of nuclei, plotting area against volume."""
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    layer = (await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area", "yColumn": "volume"}}))["createPointsChartLayer"]
    return chart, layer, lens


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scatter_plots_other_columns(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, layer, _ = await _scatter(ctx)

    # The layer's own old x (µm²) is not another layer: a new unit on its only layer is fine.
    moved = (await _ok(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "xColumn": "intensity", "zColumn": "area", "markerSize": 6}}))["updatePointsChartLayer"]
    assert (moved["xColumn"], moved["yColumn"], moved["zColumn"]) == ("intensity", "volume", "area"), "y kept"
    assert (moved["xUnit"], moved["markerSize"]) == (None, 6)

    flat = (await _ok(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "clearZColumn": True}}))["updatePointsChartLayer"]
    assert flat["zColumn"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_edit_is_held_to_the_rules_of_creation(authenticated_context: HttpContext):
    ctx = authenticated_context
    chart, layer, lens = await _scatter(ctx)
    assert "holds VARCHAR, not numbers" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "xColumn": "condition"}})
    assert "a different column for each position" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "xColumn": "volume"}}), "against the stored y"
    assert "both set and cleared" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "zColumn": "area_mm", "clearZColumn": True}})
    assert "MARKERS, LINE" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "mark": "BOX"}})

    # With a second layer, units are checked against it -- and only against it.
    await _ok(ctx, POINTS, {"input": {"chart": chart["id"], "lens": lens, "xColumn": "area_mm", "yColumn": "volume"}})
    assert "One axis of a chart is one quantity" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "xColumn": "intensity"}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_is_updated_by_its_own_kind(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, layer, _ = await _scatter(ctx)
    message = await _refused(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "valueColumn": "area"}})
    assert "is a POINTS layer, not a DISTRIBUTION layer" in message and "Use updatePointsChartLayer" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_distribution_is_regrouped_rebinned_and_boxed(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    layer = (await _ok(ctx, DISTRIBUTION, {"input": {"chart": chart["id"], "lens": lens, "valueColumn": "area", "bins": 20}}))["createDistributionChartLayer"]

    grouped = (await _ok(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "groupColumn": "condition", "bins": 40}}))["updateDistributionChartLayer"]
    assert (grouped["groupColumn"], grouped["bins"], grouped["mark"]) == ("condition", 40, "HISTOGRAM")
    # Held to the merged state: the stored grouping is `condition`, so summarising it is grouping it by itself.
    assert "not grouped by 'condition' itself" in await _refused(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "valueColumn": "condition"}})
    assert "A measurement has no values to count" in await _refused(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "groupColumn": "volume"}})

    boxed = (await _ok(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "mark": "BOX"}}))["updateDistributionChartLayer"]
    assert (boxed["mark"], boxed["bins"]) == ("BOX", None), "a box has no bins: the stored count goes with the histogram"
    assert "a BOX has none" in await _refused(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "bins": 10}})

    whole = (await _ok(ctx, UPDATE_DISTRIBUTION, {"input": {"id": layer["id"], "clearGroupColumn": True, "mark": "HISTOGRAM"}}))["updateDistributionChartLayer"]
    assert (whole["groupColumn"], whole["mark"]) == (None, "HISTOGRAM")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_is_rebound_only_where_its_rows_are_placed(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, layer, lens = await _scatter(ctx)
    _, other_lens, _ = await _nuclei(ctx, "unrelated nuclei")
    assert "the world of chart" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "lens": other_lens}})

    # The current lens again is no rebinding, and is not re-litigated.
    kept = (await _ok(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "lens": lens, "markerSize": 3}}))["updatePointsChartLayer"]
    assert kept["markerSize"] == 3

    # A gate on the same table draws in the same world.
    gate = (await _ok(ctx, GATE, {"input": {"tableDataset": kept["tableDataset"]["id"], "windows": [{"axis": "area", "min": 50}]}}))["createTableLens"]["id"]
    gated = (await _ok(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "lens": gate}}))["updatePointsChartLayer"]
    assert gated["xColumn"] == "area"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_series_reads_another_value_column(authenticated_context: HttpContext):
    ctx = authenticated_context
    chart = await _chart(ctx)
    table = await _series_table(ctx, "measurements", [*_MEASUREMENTS, {"name": "background", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    await _register(ctx, table["coordinateSystem"]["id"], chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "parts": [{"kind": "IDENTITY", "inputAxes": ["x"], "outputAxes": ["d"]}]})
    made = (await _ok_input(ctx, SERIES, {"chart": chart["id"], "lens": await seed.whole_lens_id(models.TableDataset, table["id"]), "valueColumn": "intensity"}))["createSeriesChartLayer"]

    updated = (await _ok(ctx, UPDATE_SERIES, {"input": {"id": made["id"], "valueColumn": "background"}}))["updateSeriesChartLayer"]
    assert (updated["valueColumn"], updated["valueUnit"]) == ("background", None)
    assert "is the coordinate column" in await _refused(ctx, UPDATE_SERIES, {"input": {"id": made["id"], "valueColumn": "x"}})
    assert "Use updateSeriesChartLayer" in await _refused(ctx, UPDATE_TRACE, {"input": {"id": made["id"], "markerSize": 4}})
    assert "is a SERIES layer, not a POINTS layer" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": made["id"], "xColumn": "intensity"}})
