"""A chart remembers how its layers share the screen: `Chart.layout`.

STACKED gives each layer a row (AXIS) or a panel (VALUES) of its own; SHARED (the default)
overlays the layers that measure the same thing and lays the rest beside them; OVERLAY puts
every layer in one plot on its own scale, which only an AXIS chart can draw -- in a chart of
values a layer's scale IS its axis.
"""

import pytest
from kante.context import HttpContext

from tests.test_chart_values import _nuclei, _ok, _refused, _values_chart

LAYOUT = """
mutation L($input: UpdateChartInput!) { updateChart(input: $input) { id layout } }
"""

CHART = """
mutation C($input: CreateChartInput!) { createChart(input: $input) { id kind layout } }
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
    assert (axis["kind"], axis["layout"]) == ("AXIS", "OVERLAY")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_of_values_has_no_overlay(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, _, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    assert "Use STACKED" in await _refused(ctx, LAYOUT, {"input": {"id": chart["id"], "layout": "OVERLAY"}})
    assert "Use STACKED" in await _refused(ctx, CHART, {"input": {"name": "m", "kind": "VALUES", "coordinateSystem": space, "layout": "OVERLAY"}})
