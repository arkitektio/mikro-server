"""A chart remembers how its layers share the screen: `Chart.layout`.

STACKED gives each layer a row (along the axis) or a panel (by its values) of its own; SHARED
(the default) overlays the layers that read their axes alike and lays the rest beside them;
OVERLAY puts every along-axis layer in one plot on its own scale, while value layers -- whose
scale IS their axis -- lay out as SHARED. Any layout is accepted by any chart.
"""

import pytest
from kante.context import HttpContext

from tests.test_chart_values import _nuclei, _ok, _values_chart

LAYOUT = """
mutation L($input: UpdateChartInput!) { updateChart(input: $input) { id layout } }
"""

CHART = """
mutation C($input: CreateChartInput!) { createChart(input: $input) { id layout } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_remembers_its_layout(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, _, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    stacked = (await _ok(ctx, LAYOUT, {"input": {"id": chart["id"], "layout": "STACKED"}}))["updateChart"]
    assert stacked["layout"] == "STACKED"

    axis = (await _ok(ctx, CHART, {"input": {"name": "profile", "axis": {"name": "t", "type": "TIME", "unit": "second"}, "layout": "OVERLAY"}}))["createChart"]
    assert axis["layout"] == "OVERLAY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_any_chart_takes_any_layout(authenticated_context: HttpContext):
    """OVERLAY over a table's rows is accepted: it only ever puts along-axis layers in one plot."""
    ctx = authenticated_context
    _, _, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    assert (await _ok(ctx, LAYOUT, {"input": {"id": chart["id"], "layout": "OVERLAY"}}))["updateChart"]["layout"] == "OVERLAY"
