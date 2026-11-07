"""How a chart layer looks: sizes as multiples of the chart's default, and the marker's symbol.

`lineWidth` and `markerSize` are never pixels: 1 is the chart's default, 2 twice it, 0.5 half --
what the default is on screen is the viewer's, so a chart reads the same on a laptop, a 4K wall
and in a figure. `markerShape` names the symbol: CIRCLE (the viewer's default), SQUARE,
TRIANGLE, DIAMOND, CROSS or PLUS.
"""

import importlib

import pytest
from asgiref.sync import sync_to_async
from django.apps import apps
from kante.context import HttpContext

from chart import models as chart_models
from tests.test_chart_layer_updates import _scatter
from tests.test_chart_values import _ok, _refused

UPDATE_POINTS = """
mutation U($input: UpdatePointsChartLayerInput!) { updatePointsChartLayer(input: $input) { id lineWidth markerSize markerShape } }
"""

RESTYLE = """
mutation R($input: UpdateChartLayerInput!) { updateChartLayer(input: $input) { id ... on PointsChartLayer { markerSize markerShape } } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sizes_are_multiples_and_the_marker_has_a_shape(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, layer, _ = await _scatter(ctx)
    styled = (await _ok(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "markerSize": 2.0, "lineWidth": 0.5, "markerShape": "SQUARE"}}))["updatePointsChartLayer"]
    assert (styled["markerSize"], styled["lineWidth"], styled["markerShape"]) == (2.0, 0.5, "SQUARE")

    restyled = (await _ok(ctx, RESTYLE, {"input": {"id": layer["id"], "markerShape": "TRIANGLE"}}))["updateChartLayer"]
    assert (restyled["markerShape"], restyled["markerSize"]) == ("TRIANGLE", 2.0), "a restyle leaves the size it does not name"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_size_is_a_positive_multiple(authenticated_context: HttpContext):
    ctx = authenticated_context
    _, layer, _ = await _scatter(ctx)
    assert "greater than 0" in await _refused(ctx, UPDATE_POINTS, {"input": {"id": layer["id"], "markerSize": 0}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stored_pixels_become_multiples_of_the_old_defaults(authenticated_context: HttpContext):
    """Before 0005 a size was pixels against defaults of 1.25 px (line) and 5 px (marker): 2.5 px and 10 px are twice the default."""
    ctx = authenticated_context
    _, layer, _ = await _scatter(ctx)
    migration = importlib.import_module("chart.migrations.0005_sizes_as_multiples_of_the_default")

    def convert() -> tuple[float, float, float, float]:
        chart_models.ChartLayer.objects.filter(pk=layer["id"]).update(line_width=2.5, marker_size=10.0)
        migration.to_multiples(apps, None)
        multiples = chart_models.ChartLayer.objects.values_list("line_width", "marker_size").get(pk=layer["id"])
        migration.to_pixels(apps, None)
        pixels = chart_models.ChartLayer.objects.values_list("line_width", "marker_size").get(pk=layer["id"])
        return (*multiples, *pixels)

    assert await sync_to_async(convert)() == (2.0, 2.0, 2.5, 10.0)
