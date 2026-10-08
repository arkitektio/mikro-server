"""A chart: data laid out along one metric axis, with values read off it.

The second composition beside a scene. These tests pin what makes it one: it adopts a world
with a single metric axis and never owns it; each layer kind is defined by what it reads;
and where a layer's data sits along the axis is the graph's answer, read through edges and
stored on no row -- so the tests about placement change an *edge* and watch the layer move.

They also pin that the data layer treats charts and scenes as equals: a space a chart is
laid out over is as undeletable, and as unsweepable, as one a scene is composed over, by the
same registry and with no knowledge of either.
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from datalayer.models import ZarrStore
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from chart import models as chart_models
from core import enums, models
from mikro_server.schema import schema
from tests import seed

CREATE_CHART = """
mutation CreateChart($input: CreateChartInput!) {
  createChart(input: $input) {
    id
    name
    descriptors
    axis { name type unit }
    worldCoordinateSystem { id axes { name } }
    layers { id }
  }
}
"""

CHART_FROM_SYSTEM = """
mutation FromSystem($input: CreateChartFromCoordinateSystemInput!) {
  createChartFromCoordinateSystem(input: $input) { id name layers { __typename kind name } }
}
"""

TRACE = """
mutation Trace($input: CreateTraceChartLayerInput!) {
  createTraceChartLayer(input: $input) { id kind mark alongAxis seriesAxis lens { id } }
}
"""

SERIES = """
mutation Series($input: CreateSeriesChartLayerInput!) {
  createSeriesChartLayer(input: $input) { id kind valueColumn coordinateColumn valueUnit alongAxis }
}
"""

ANNOTATION_LAYER = """
mutation AnnotationLayer($input: CreateAnnotationChartLayerInput!) {
  createAnnotationChartLayer(input: $input) {
    id
    kind
    alongAxis
    annotationCollection { id coordinateSystem { id axes { name type unit } } }
  }
}
"""

UPDATE_LAYER = """
mutation Update($input: UpdateChartLayerInput!) {
  updateChartLayer(input: $input) { __typename id kind ... on TraceChartLayer { mark lineWidth } }
}
"""

CHART = """
query Chart($id: ID!) {
  chart(id: $id) {
    id
    axis { name type unit }
    layers {
      __typename
      id
      kind
      alongAxis
      placement
      placementValidity
      asAffine { matrix inputAxes outputAxes total }
      pathToWorld { inverted transformation { id kind } }
      ... on TraceChartLayer { seriesAxis mark }
      ... on SeriesChartLayer { valueColumn coordinateColumn }
      ... on AnnotationChartLayer { annotationCollection { id } }
    }
  }
}
"""

REGISTER = """
mutation Register($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id }
}
"""

DELETE_TRANSFORMATION = "mutation D($input: DeleteTransformationInput!) { deleteTransformation(input: $input) }"
DELETE_SYSTEM = "mutation D($input: DeleteCoordinateSystemInput!) { deleteCoordinateSystem(input: $input) }"
DELETE_CHART = "mutation D($input: DeleteChartInput!) { deleteChart(input: $input) }"
SWEEP = "mutation { deleteOrphanedCoordinateSystems }"

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id coordinateSystem { id } }
}
"""

CREATE_COLLECTION = """
mutation M($input: CreateAnnotationCollectionInput!) {
  createAnnotationCollection(input: $input) { id coordinateSystem { id axes { name type } } }
}
"""

DERIVE = """
mutation Derive($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id derivedFrom { id kind } }
}
"""

CREATE_ANNOTATION = """
mutation Create($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id intrinsicBbox { min max } }
}
"""

_MICROMETRE_AXIS = {"name": "d", "type": "SPACE", "unit": "micrometer"}


def _fresh(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity: the chart-graph memo lives on the context."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


async def _run(ctx: HttpContext, document: str, input: dict | None = None, **variables: object):
    if input is not None:
        variables["input"] = input
    return await schema.execute(document, context_value=_fresh(ctx), variable_values=variables or None)


async def _ok(ctx: HttpContext, document: str, input: dict | None = None, **variables: object) -> dict:
    result = await _run(ctx, document, input, **variables)
    assert not result.errors, result.errors
    return result.data


async def _chart(ctx: HttpContext, name: str = "Profile", axis: dict | None = None) -> dict:
    """A chart over a world minted from one axis: micrometres along `d` unless told otherwise."""
    return (await _ok(ctx, CREATE_CHART, {"name": name, "axis": axis or _MICROMETRE_AXIS}))["createChart"]


async def _register(ctx: HttpContext, input_id: object, output_id: object, transform: dict) -> str:
    data = await _ok(ctx, REGISTER, {"input": str(input_id), "output": str(output_id), "transform": transform})
    return str(data["createTransformation"]["id"])


async def _dataset(ctx: HttpContext, name: str, axes: list[tuple[str, enums.AxisType]], shape: list[int]) -> models.ArrayDataset:
    return await seed.create_array_dataset(ctx, name, axes=[seed.axis(axis_name, axis_type) for axis_name, axis_type in axes], shapes=[shape])


async def _system_of(dataset: models.ArrayDataset) -> models.CoordinateSystem:
    return await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()


async def _lay_along(ctx: HttpContext, dataset: models.ArrayDataset, chart: dict, axis: str, *, scale: float = 0.25, offset: float = 0.0) -> str:
    """Author the one fact that places a dataset in a chart: one of its axes, scaled onto the chart's."""
    system = await _system_of(dataset)
    chart_axis = chart["axis"]["name"]
    return await _register(
        ctx,
        system.pk,
        chart["worldCoordinateSystem"]["id"],
        {"kind": "BY_DIMENSION", "inputAxes": [axis], "outputAxes": [chart_axis], "affine": [[scale, offset]]},
    )


async def _layers(ctx: HttpContext, chart_id: str) -> list[dict]:
    return (await _ok(ctx, CHART, id=chart_id))["chart"]["layers"]


# --- the chart and its world ---------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_is_created_over_a_world_with_one_axis(authenticated_context: HttpContext):
    """The whole of a chart at birth: a name, and the one axis it is laid out along."""
    chart = await _chart(authenticated_context)

    assert chart["axis"] == {"name": "d", "type": "SPACE", "unit": "micrometer"}
    assert [axis["name"] for axis in chart["worldCoordinateSystem"]["axes"]] == ["d"]
    assert chart["layers"] == []
    # What the hub is told about it: the structure mikro's contract declares, described by its axis.
    assert chart["descriptors"] == {"@mikro/axis_type": "SPACE", "@mikro/axis_unit": "micrometer"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "axis",
    [
        {"name": "t", "type": "TIME", "unit": "second"},
        {"name": "wavelength", "type": "SPECTRUM", "unit": "nanometer"},
        {"name": "arrival", "type": "MICROTIME", "unit": "nanosecond"},
    ],
    ids=["time", "spectrum", "microtime"],
)
async def test_the_axis_is_any_metric_axis_not_only_space(authenticated_context: HttpContext, axis: dict):
    """A chart assumes nothing about what its axis measures -- least of all that it is time."""
    chart = await _chart(authenticated_context, axis=axis)
    assert chart["axis"]["type"] == axis["type"]
    assert chart["axis"]["name"] == axis["name"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_charts_adopt_one_world_and_neither_owns_it(authenticated_context: HttpContext):
    """Adopting is naming a space, not taking it: the second chart is over the same one."""
    ctx = authenticated_context
    first = await _chart(ctx, "First")
    world_id = first["worldCoordinateSystem"]["id"]

    second = (await _ok(ctx, CREATE_CHART, {"name": "Second", "coordinateSystem": world_id}))["createChart"]
    assert second["worldCoordinateSystem"]["id"] == world_id

    # Deleting one leaves the space, and the other chart over it.
    await _ok(ctx, DELETE_CHART, {"id": first["id"]})
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=world_id).exists)()
    assert await sync_to_async(chart_models.Chart.objects.filter(pk=second["id"]).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_world_that_is_not_one_metric_unit_carrying_axis_is_refused(authenticated_context: HttpContext):
    """Three ways a space is not an axis to lay data along, each refused by name."""
    ctx = authenticated_context

    # Several axes: a place, not an axis.
    scene = await seed.create_scene(ctx, "Place")
    several = await _run(ctx, CREATE_CHART, {"name": "C", "coordinateSystem": str(scene.world_id)})
    assert several.errors and "laid out along exactly one" in str(several.errors[0]), several.errors

    # One axis with no unit: a pixel grid's.
    grid = await _dataset(ctx, "Grid", [("d", enums.AxisType.SPACE)], [64])
    unitless = await _run(ctx, CREATE_CHART, {"name": "C", "coordinateSystem": str((await _system_of(grid)).pk)})
    assert unitless.errors and "carries no unit" in str(unitless.errors[0]), unitless.errors

    # One axis that enumerates: there is no distance between object 3 and object 4.
    enumerating = await _run(ctx, CREATE_CHART, {"name": "C", "axis": {"name": "object", "type": "INDEX", "unit": "a.u."}})
    assert enumerating.errors and "does not measure anything" in str(enumerating.errors[0]), enumerating.errors

    # And a chart over nothing, or over two things at once, is not a chart.
    neither = await _run(ctx, CREATE_CHART, {"name": "C"})
    assert neither.errors and "exactly one of" in str(neither.errors[0]), neither.errors
    assert not await sync_to_async(chart_models.Chart.objects.exists)(), "a refused chart leaves no chart and no minted space behind"


# --- TRACE ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_one_dimensional_dataset_is_drawn_as_a_trace(authenticated_context: HttpContext):
    """A lens with one metric axis free, laid along the chart by the registration."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    profile = await _dataset(ctx, "Profile", [("d", enums.AxisType.SPACE)], [64])
    await _lay_along(ctx, profile, chart, "d", scale=0.25)

    made = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk)}))["createTraceChartLayer"]
    assert made["kind"] == "TRACE"
    assert made["mark"] == "LINE"
    assert made["alongAxis"] == "d"
    assert made["seriesAxis"] is None

    (layer,) = await _layers(ctx, chart["id"])
    assert layer["__typename"] == "TraceChartLayer"
    assert layer["placement"] == "PLACED"
    # One row, because the world has one axis: a pixel is a quarter of a micrometre.
    assert layer["asAffine"] == {"matrix": [[0.25, 0.0]], "inputAxes": ["d"], "outputAxes": ["d"], "total": True}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_free_channel_axis_is_the_series_axis(authenticated_context: HttpContext):
    """One enumerating axis may stay free beside the one read along: a line per position."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    spectra = await _dataset(ctx, "Profiles", [("c", enums.AxisType.CHANNEL), ("d", enums.AxisType.SPACE)], [3, 64])
    await _lay_along(ctx, spectra, chart, "d")

    made = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(spectra.pk)}))["createTraceChartLayer"]
    assert made["alongAxis"] == "d"
    assert made["seriesAxis"] == "c"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_second_free_metric_axis_is_a_surface_not_a_trace(authenticated_context: HttpContext):
    """An image is placed along the axis and is still not a trace; a row of it is."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    image = await _dataset(ctx, "Image", [("y", enums.AxisType.SPACE), ("x", enums.AxisType.SPACE)], [32, 64])
    await _lay_along(ctx, image, chart, "x")

    whole = await _run(ctx, TRACE, {"chart": chart["id"], "dataset": str(image.pk)})
    assert whole.errors, "y is free and metric: that is a surface"
    assert "'y' (32)" in str(whole.errors[0]) and "slice the lens" in str(whole.errors[0]), whole.errors

    row = await seed.create_lens(ctx, image, slices=[{"axis": "y", "start": 5, "stop": 6, "step": None}])
    made = (await _ok(ctx, TRACE, {"chart": chart["id"], "lens": str(row.pk)}))["createTraceChartLayer"]
    assert made["alongAxis"] == "x"
    assert made["seriesAxis"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_fixed_along_the_charts_axis_has_no_trace(authenticated_context: HttpContext):
    """One sample along the axis the chart is laid out along is a point, not a line."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    profile = await _dataset(ctx, "Profile", [("d", enums.AxisType.SPACE)], [64])
    await _lay_along(ctx, profile, chart, "d")
    sample = await seed.create_lens(ctx, profile, slices=[{"axis": "d", "start": 5, "stop": 6, "step": None}])

    refused = await _run(ctx, TRACE, {"chart": chart["id"], "lens": str(sample.pk)})
    assert refused.errors and "single position along 'd'" in str(refused.errors[0]), refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_how_a_trace_looks_is_a_setting_not_a_kind(authenticated_context: HttpContext):
    """Line, markers, both, steps: the row's `mark` changes and its kind does not."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    profile = await _dataset(ctx, "Profile", [("d", enums.AxisType.SPACE)], [64])
    await _lay_along(ctx, profile, chart, "d")
    layer_id = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk), "mark": "MARKERS"}))["createTraceChartLayer"]["id"]

    for mark in ("LINE", "LINE_MARKERS", "STEPS"):
        updated = (await _ok(ctx, UPDATE_LAYER, {"id": layer_id, "mark": mark, "lineWidth": 2.0}))["updateChartLayer"]
        assert updated == {"__typename": "TraceChartLayer", "id": layer_id, "kind": "TRACE", "mark": mark, "lineWidth": 2.0}


# --- SERIES --------------------------------------------------------------------

_MEASUREMENTS = [
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "nanometer"},
    {"name": "intensity", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "volt"},
    {"name": "note", "dtype": "VARCHAR", "role": "ATTRIBUTE"},
]


async def _table(ctx: HttpContext, name: str, columns: list[dict]) -> dict:
    return (await _ok(ctx, CREATE_TABLE, await seed.table_input(ctx, name, columns)))["createTableDataset"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_is_drawn_as_a_series(authenticated_context: HttpContext):
    """One coordinate column along the axis -- the graph says which -- and one numeric value column."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    table = await _table(ctx, "measurements", _MEASUREMENTS)
    # Nanometres in the table, micrometres on the chart. The edge states the factor: a named
    # axis of a BY_DIMENSION is not unit-converted for its author.
    await _register(ctx, table["coordinateSystem"]["id"], chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "inputAxes": ["x"], "outputAxes": ["d"], "affine": [[0.001, 0.0]]})

    made = (await _ok(ctx, SERIES, {"chart": chart["id"], "tableDataset": table["id"]}))["createSeriesChartLayer"]
    assert made["kind"] == "SERIES"
    assert made["valueColumn"] == "intensity", "the only numeric attribute, so it need not be named"
    assert made["coordinateColumn"] == "x"
    assert made["alongAxis"] == "x"
    assert made["valueUnit"] == "volt"

    (layer,) = await _layers(ctx, chart["id"])
    assert layer["__typename"] == "SeriesChartLayer"
    # The factor is the edge's, not a column of the layer or the chart.
    assert layer["asAffine"]["matrix"] == [[pytest.approx(0.001), 0.0]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_series_value_is_one_numeric_attribute_column(authenticated_context: HttpContext):
    """Inferred only when there is one to infer; otherwise named, and checked."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    columns = [*_MEASUREMENTS, {"name": "background", "dtype": "DOUBLE", "role": "ATTRIBUTE"}]
    table = await _table(ctx, "measurements", columns)
    await _register(ctx, table["coordinateSystem"]["id"], chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "inputAxes": ["x"], "outputAxes": ["d"]})

    ambiguous = await _run(ctx, SERIES, {"chart": chart["id"], "tableDataset": table["id"]})
    assert ambiguous.errors and "cannot be inferred" in str(ambiguous.errors[0]), ambiguous.errors

    text = await _run(ctx, SERIES, {"chart": chart["id"], "tableDataset": table["id"], "valueColumn": "note"})
    assert text.errors and "not a numeric attribute column" in str(text.errors[0]), text.errors

    coordinate = await _run(ctx, SERIES, {"chart": chart["id"], "tableDataset": table["id"], "valueColumn": "x"})
    assert coordinate.errors and "is the coordinate column" in str(coordinate.errors[0]), coordinate.errors

    made = (await _ok(ctx, SERIES, {"chart": chart["id"], "tableDataset": table["id"], "valueColumn": "background"}))["createSeriesChartLayer"]
    assert made["valueColumn"] == "background"
    assert made["valueUnit"] is None


# --- ANNOTATION ----------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_makes_a_drawing_surface_with_a_value_axis(authenticated_context: HttpContext):
    """Marks sit at a position and a height: the chart's axis, and a VALUE axis beside it."""
    ctx = authenticated_context
    chart = await _chart(ctx)

    made = (await _ok(ctx, ANNOTATION_LAYER, {"chart": chart["id"], "name": "Peaks"}))["createAnnotationChartLayer"]
    assert made["kind"] == "ANNOTATION"
    axes = made["annotationCollection"]["coordinateSystem"]["axes"]
    assert axes == [{"name": "d", "type": "SPACE", "unit": None}, {"name": "value", "type": "VALUE", "unit": None}]
    # Only the chart's own axis is registered into the world: it has nothing to say about height.
    assert made["alongAxis"] == "d"

    mark = await _ok(ctx, CREATE_ANNOTATION, {"collection": made["annotationCollection"]["id"], "kind": "POINT", "vectors": [[4.0, 120.0]]})
    box = mark["createAnnotation"]["intrinsicBbox"]
    assert box["min"] == [3.5, 119.5] and box["max"] == [4.5, 120.5], "the mark is at a position and a height, in the drawing space"

    (layer,) = await _layers(ctx, chart["id"])
    assert layer["__typename"] == "AnnotationChartLayer"
    assert layer["placement"] == "PLACED"
    assert layer["placementValidity"] == "VALIDATED", "exact by construction"
    assert layer["asAffine"] == {"matrix": [[1.0, 0.0, 0.0]], "inputAxes": ["d", "value"], "outputAxes": ["d"], "total": True}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_existing_collection_is_drawn_once(authenticated_context: HttpContext):
    """A collection drawn elsewhere is a layer here when the graph places it, and only one."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    collection = (await _ok(ctx, CREATE_COLLECTION, {"name": "Events", "axes": [{"name": "d", "type": "SPACE"}]}))["createAnnotationCollection"]

    unplaced = await _run(ctx, ANNOTATION_LAYER, {"chart": chart["id"], "annotationCollection": collection["id"]})
    assert unplaced.errors and "Nothing places" in str(unplaced.errors[0]), unplaced.errors

    await _register(ctx, collection["coordinateSystem"]["id"], chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "inputAxes": ["d"], "outputAxes": ["d"]})
    await _ok(ctx, ANNOTATION_LAYER, {"chart": chart["id"], "annotationCollection": collection["id"]})

    twice = await _run(ctx, ANNOTATION_LAYER, {"chart": chart["id"], "annotationCollection": collection["id"]})
    assert twice.errors and "already drawn" in str(twice.errors[0]), twice.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_value_axis_exists_only_in_a_drawing_space(authenticated_context: HttpContext):
    """Data holds its values; it has no axis of them. Nor has a world."""
    ctx = authenticated_context

    with pytest.raises(ValueError, match="VALUE axis"):
        await _dataset(ctx, "Signal", [("d", enums.AxisType.SPACE), ("v", enums.AxisType.VALUE)], [64, 2])

    world = await _run(ctx, CREATE_CHART, {"name": "C", "axis": {"name": "v", "type": "VALUE", "unit": "volt"}})
    assert world.errors and "VALUE axis" in str(world.errors[0]), world.errors

    # An annotation collection's space is where one belongs, whoever creates it.
    collection = await _ok(ctx, CREATE_COLLECTION, {"name": "Marks", "axes": [{"name": "t", "type": "TIME"}, {"name": "v", "type": "VALUE"}]})
    assert [axis["type"] for axis in collection["createAnnotationCollection"]["coordinateSystem"]["axes"]] == ["TIME", "VALUE"]


# --- placement is the graph's ---------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_data_nothing_places_along_the_axis_is_refused(authenticated_context: HttpContext):
    """A layer is a claim the graph must already hold. No edge, no layer -- and none is written."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    profile = await _dataset(ctx, "Profile", [("d", enums.AxisType.SPACE)], [64])

    refused = await _run(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk)})
    assert refused.errors and "Nothing places" in str(refused.errors[0]), refused.errors
    assert "the axis of chart 'Profile'" in str(refused.errors[0])
    assert not await sync_to_async(models.Transformation.objects.filter(output_id=chart["worldCoordinateSystem"]["id"]).exists)(), "no registration was fabricated"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reaching_the_axis_through_a_field_is_not_placement(authenticated_context: HttpContext):
    """Mikro's strict rule, unchanged: a route with no closed form places nothing a chart can draw."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    signal = await _dataset(ctx, "Signal", [("d", enums.AxisType.SPACE)], [64])
    objects = await _dataset(ctx, "Objects", [("i", enums.AxisType.INDEX)], [128])
    signal_system, object_system = await _system_of(signal), await _system_of(objects)

    field_id = await _register(ctx, signal_system.pk, object_system.pk, {"kind": "FIELD", "field": str(signal_system.pk), "inputAxes": ["d"], "outputAxes": ["i"]})
    await _register(ctx, object_system.pk, chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "inputAxes": ["i"], "outputAxes": ["d"]})

    refused = await _run(ctx, TRACE, {"chart": chart["id"], "dataset": str(signal.pk)})
    assert refused.errors, "reachable, and still not placed"
    assert "affinely" in str(refused.errors[0]) and f"{field_id} (FIELD)" in str(refused.errors[0]), refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_route_that_says_nothing_about_the_axis_lays_nothing_along_it(authenticated_context: HttpContext):
    """Placed in the world by the strict rule, and still nowhere along its axis.

    The data reaches the world through a frame with two axes: it is registered onto one, and
    the frame is registered into the world by the *other*. Every step is affine, so the gate
    passes -- and the composed map never mentions the chart's axis.
    """
    ctx = authenticated_context
    chart = await _chart(ctx)
    profile = await _dataset(ctx, "Profile", [("a", enums.AxisType.SPACE)], [64])
    frame = (
        await _ok(
            ctx,
            "mutation M($input: CreateCoordinateSystemInput!) { createCoordinateSystem(input: $input) { id } }",
            {"name": "frame", "axes": [{"name": "d", "type": "SPACE", "unit": "micrometer"}, {"name": "e", "type": "SPACE", "unit": "micrometer"}]},
        )
    )["createCoordinateSystem"]["id"]
    await _register(ctx, (await _system_of(profile)).pk, frame, {"kind": "BY_DIMENSION", "inputAxes": ["a"], "outputAxes": ["e"], "affine": [[0.25, 0.0]]})
    await _register(ctx, frame, chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "inputAxes": ["d"], "outputAxes": ["d"]})

    refused = await _run(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk)})
    assert refused.errors and "says nothing about the axis 'd'" in str(refused.errors[0]), refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_map_reading_the_axis_from_two_data_axes_lays_neither_along_it(authenticated_context: HttpContext):
    """An oblique map puts a diagonal of the image on the axis: no single axis runs along the chart."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    image = await _dataset(ctx, "Image", [("y", enums.AxisType.SPACE), ("x", enums.AxisType.SPACE)], [1, 64])
    await _register(ctx, (await _system_of(image)).pk, chart["worldCoordinateSystem"]["id"], {"kind": "AFFINE", "affine": [[1.0, 1.0, 0.0]]})

    refused = await _run(ctx, TRACE, {"chart": chart["id"], "dataset": str(image.pk)})
    assert refused.errors and "from 2 of the data's axes (y, x)" in str(refused.errors[0]), refused.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_where_a_layer_sits_follows_the_edge_with_no_write_to_the_layer(authenticated_context: HttpContext):
    """The axis laid along the chart, its scale and its offset are read from the graph each time.

    The layer row is written once. Then the registration is replaced -- another scale, then
    another *axis* -- and every answer about the layer changes while the row does not.
    """
    ctx = authenticated_context
    chart = await _chart(ctx)
    # Both axes are metric; `b` has one position, so the lens is a trace along `a`.
    data = await _dataset(ctx, "Data", [("a", enums.AxisType.SPACE), ("b", enums.AxisType.SPACE)], [64, 1])
    first = await _lay_along(ctx, data, chart, "a", scale=0.25)
    layer_id = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(data.pk)}))["createTraceChartLayer"]["id"]
    row = await sync_to_async(lambda: chart_models.ChartLayer.objects.filter(pk=layer_id).values().get())()

    (layer,) = await _layers(ctx, chart["id"])
    assert layer["alongAxis"] == "a"
    assert layer["asAffine"]["matrix"] == [[0.25, 0.0, 0.0]]
    assert [step["transformation"]["id"] for step in layer["pathToWorld"]] == [first]

    # A better calibration: same axis, another scale and an offset.
    await _ok(ctx, DELETE_TRANSFORMATION, {"id": first})
    (layer,) = await _layers(ctx, chart["id"])
    assert layer["placement"] == "UNREGISTERED" and layer["alongAxis"] is None and layer["asAffine"] is None

    second = await _lay_along(ctx, data, chart, "a", scale=0.5, offset=10.0)
    (layer,) = await _layers(ctx, chart["id"])
    assert layer["alongAxis"] == "a"
    assert layer["asAffine"]["matrix"] == [[0.5, 0.0, 10.0]]

    # And another axis altogether: nothing on the layer named `a`, so nothing holds it there.
    await _ok(ctx, DELETE_TRANSFORMATION, {"id": second})
    await _lay_along(ctx, data, chart, "b", scale=2.0)
    (layer,) = await _layers(ctx, chart["id"])
    assert layer["alongAxis"] == "b"
    assert layer["asAffine"]["matrix"] == [[0.0, 2.0, 0.0]]

    assert await sync_to_async(lambda: chart_models.ChartLayer.objects.filter(pk=layer_id).values().get())() == row, "not one column of the layer changed"


# --- charts and scenes are equals to the data layer -----------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_a_chart_is_laid_out_over_cannot_be_deleted(authenticated_context: HttpContext):
    """The refusal a scene's world gets, in the same words, from the same registry."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    world_id = chart["worldCoordinateSystem"]["id"]

    refused = await _run(ctx, DELETE_SYSTEM, {"id": world_id})
    assert refused.errors, "a chart's world outlives the request to delete it"
    assert f"is the world of 1 chart(s) ({chart['id']})" in str(refused.errors[0]), refused.errors

    await _ok(ctx, DELETE_CHART, {"id": chart["id"]})
    await _ok(ctx, DELETE_SYSTEM, {"id": world_id})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_orphan_sweep_spares_a_charts_world(authenticated_context: HttpContext):
    """Nothing lives in a chart's world and no edge need touch it: only the chart keeps it."""
    ctx = authenticated_context
    chart = await _chart(ctx)
    world_id = chart["worldCoordinateSystem"]["id"]

    swept = (await _ok(ctx, SWEEP))["deleteOrphanedCoordinateSystems"]
    assert world_id not in swept
    assert await sync_to_async(chart_models.Chart.objects.filter(pk=chart["id"]).exists)()

    # Once no chart is over it, it is the garbage the sweep exists to collect.
    await _ok(ctx, DELETE_CHART, {"id": chart["id"]})
    assert world_id in (await _ok(ctx, SWEEP))["deleteOrphanedCoordinateSystems"]


# --- a chart names data and owns none -------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_chart_deletes_nothing_it_drew_and_deleting_data_takes_its_layer(authenticated_context: HttpContext):
    ctx = authenticated_context
    chart = await _chart(ctx)
    kept = await _dataset(ctx, "Kept", [("d", enums.AxisType.SPACE)], [64])
    gone = await _dataset(ctx, "Gone", [("d", enums.AxisType.SPACE)], [64])
    for dataset in (kept, gone):
        await _lay_along(ctx, dataset, chart, "d")
    kept_layer = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(kept.pk)}))["createTraceChartLayer"]
    gone_layer = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(gone.pk)}))["createTraceChartLayer"]

    # Data going away takes the layers that drew it, and nothing else.
    await sync_to_async(models.Lens.objects.filter(pk=gone_layer["lens"]["id"]).delete)()
    assert [layer["id"] for layer in await _layers(ctx, chart["id"])] == [kept_layer["id"]]

    # The chart going away takes its layers, and nothing they drew.
    await _ok(ctx, DELETE_CHART, {"id": chart["id"]})
    assert not await sync_to_async(chart_models.ChartLayer.objects.exists)()
    assert await sync_to_async(models.Lens.objects.filter(pk=kept_layer["lens"]["id"]).exists)()
    assert await sync_to_async(models.ArrayDataset.objects.filter(pk=kept.pk).exists)()
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=chart["worldCoordinateSystem"]["id"]).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_organization_neither_reads_nor_draws_in_a_chart(authenticated_context: HttpContext, other_org_context: HttpContext):
    chart = await _chart(authenticated_context)

    read = await schema.execute(CHART, context_value=other_org_context, variable_values={"id": chart["id"]})
    assert read.errors or read.data["chart"] is None, "another tenant's chart is not there to be read"

    listed = await schema.execute("{ charts { id } }", context_value=other_org_context)
    assert not listed.errors and listed.data["charts"] == []

    drawn = await schema.execute(ANNOTATION_LAYER, context_value=other_org_context, variable_values={"input": {"chart": chart["id"]}})
    assert drawn.errors, "nor to be drawn in"
    assert not await sync_to_async(chart_models.ChartLayer.objects.exists)()


# --- bootstrap -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_is_bootstrapped_from_what_is_laid_along_a_space(authenticated_context: HttpContext):
    """Every placed source a kind reads becomes a layer; what no kind reads is left out; no edge is written."""
    ctx = authenticated_context
    staged = await _chart(ctx, "Staging")
    world_id = staged["worldCoordinateSystem"]["id"]

    profile = await _dataset(ctx, "Profile", [("d", enums.AxisType.SPACE)], [64])
    image = await _dataset(ctx, "Image", [("y", enums.AxisType.SPACE), ("x", enums.AxisType.SPACE)], [32, 64])
    await _lay_along(ctx, profile, staged, "d")
    await _lay_along(ctx, image, staged, "x")
    await _dataset(ctx, "Elsewhere", [("d", enums.AxisType.SPACE)], [64])  # never registered
    table = await _table(ctx, "measurements", _MEASUREMENTS)
    await _register(ctx, table["coordinateSystem"]["id"], world_id, {"kind": "BY_DIMENSION", "inputAxes": ["x"], "outputAxes": ["d"]})
    edges = await sync_to_async(models.Transformation.objects.count)()

    chart = (await _ok(ctx, CHART_FROM_SYSTEM, {"coordinateSystem": world_id, "name": "Overview"}))["createChartFromCoordinateSystem"]

    assert [(layer["__typename"], layer["name"]) for layer in chart["layers"]] == [("TraceChartLayer", "Profile"), ("SeriesChartLayer", "measurements")]
    assert await sync_to_async(models.Transformation.objects.count)() == edges, "a bootstrap authors no edges"
    assert not await sync_to_async(models.Lens.objects.filter(dataset=image).exists)(), "the image was tried as a trace and left exactly as it was"


# --- end to end ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_profile_derived_from_an_image_is_charted_in_micrometres_with_a_mark_on_it(authenticated_context: HttpContext):
    """An image, a line profile taken from it, a calibration, a chart, a trace, a mark.

    Every step is a call a client makes, in the order it makes them. The chart is over the
    profile's *physical space*: it adopts the calibration that already exists rather than
    stating a second one.
    """
    ctx = authenticated_context

    # An image, and a 64-sample intensity profile computed from it -- created the way a
    # client creates derived data, naming the lens it came from.
    image = await seed.create_array_dataset(ctx, "Cells")  # (c, y, x)
    image_lens = await seed.create_lens(ctx, image)
    store = await ZarrStore.objects.acreate(organization=ctx.request.organization, key="cells-profile", bucket="zarr", shape=[64], chunks=[64], version="3", dtype="uint16", populated=True)
    # fill_info() reads zarr metadata from S3; stub it so the pre-set shape stays intact.
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        derived = await _ok(
            ctx,
            DERIVE,
            {"name": "Cells/profile", "data": str(store.id), "scales": [], "axes": [{"name": "d", "type": "SPACE"}], "derivedFrom": [{"kind": "LENS", "lens": str(image_lens.pk)}]},
        )
    # Where the profile came from is recorded, and says nothing about where it sits: a
    # derivation with no stated map is UNMAPPABLE, so it places the profile along no chart.
    assert [edge["kind"] for edge in derived["createArrayDataset"]["derivedFrom"]] == ["UNMAPPABLE"]
    profile = await models.ArrayDataset.objects.select_related("coordinate_system").aget(pk=derived["createArrayDataset"]["id"])

    # Calibrated: a sample is 0.2 um, and the profile starts 3 um in.
    distance = await seed.create_physical_space(ctx, profile, [seed.physical_axis("d", enums.AxisType.SPACE, "micrometer")], affine=[[0.2, 3.0]], name="distance")

    chart = (await _ok(ctx, CREATE_CHART, {"name": "Intensity along the cell", "coordinateSystem": str(distance.pk)}))["createChart"]
    assert chart["axis"] == {"name": "d", "type": "SPACE", "unit": "micrometer"}

    trace = (await _ok(ctx, TRACE, {"chart": chart["id"], "dataset": str(profile.pk), "name": "GFP", "mark": "LINE_MARKERS"}))["createTraceChartLayer"]
    assert trace["alongAxis"] == "d"

    # A mark on the trace: 7.4 um along, at a height of 812 counts.
    surface = (await _ok(ctx, ANNOTATION_LAYER, {"chart": chart["id"], "name": "Peaks"}))["createAnnotationChartLayer"]
    await _ok(ctx, CREATE_ANNOTATION, {"collection": surface["annotationCollection"]["id"], "kind": "POINT", "name": "membrane", "vectors": [[7.4, 812.0]]})

    read = (await _ok(ctx, CHART, id=chart["id"]))["chart"]
    drawn, marks = read["layers"]

    assert drawn["__typename"] == "TraceChartLayer" and drawn["mark"] == "LINE_MARKERS"
    assert drawn["placement"] == "PLACED"
    # Sample 22 of the profile is at 0.2 * 22 + 3 = 7.4 um: under the mark.
    assert drawn["asAffine"] == {"matrix": [[0.2, 3.0]], "inputAxes": ["d"], "outputAxes": ["d"], "total": True}
    assert [step["transformation"]["kind"] for step in drawn["pathToWorld"]] == ["AFFINE"]

    assert marks["__typename"] == "AnnotationChartLayer"
    assert marks["alongAxis"] == "d"
    assert marks["asAffine"]["matrix"] == [[1.0, 0.0, 0.0]], "the mark's position is the chart's own; its height is not the world's to place"

    annotation = await sync_to_async(lambda: models.Annotation.objects.get(collection_id=surface["annotationCollection"]["id"]))()
    assert annotation.vectors == [[7.4, 812.0]]
