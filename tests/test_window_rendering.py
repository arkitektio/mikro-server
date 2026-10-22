"""A windowed lens reaches the renderer: a point, track or series layer hands its windows on as row filters, a collection layer as a clip, and an annotation lens names the shapes it keeps.

Nothing here is stored. The server never scans a table, so what a window *selects* of one is
executed by the viewer against the parquet it reads -- the server's job is to state the rule
exactly, in the form the viewer already applies (`filterBys`), and the oracle for that rule is
the server's own inclusive window test on the anchors it does hold.
"""

import numpy as np
import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_charts import _MEASUREMENTS, _chart, _ok, _register, _table
from tests.test_column_options import _collection as _mesh_collection
from tests.test_layer_kinds import _seed_scene, _seed_table_dataset
from tests.test_lens_geometry import _collection_on, _draw

POINT_LAYER = "mutation C($input: CreatePointLayerInput!) { createPointLayer(input: $input) { id windowFilters { kind table column min max label } filterBys { column } } }"
MESH_LAYER = "mutation C($input: CreateMeshLayerInput!) { createMeshLayer(input: $input) { id clip { axis min max } } }"
SERIES_WINDOWED = "mutation S($input: CreateSeriesChartLayerInput!) { createSeriesChartLayer(input: $input) { id windowFilters { column min max } } }"
ANNOTATIONS_OF = "query A($id: ID!) { lens(id: $id) { ... on AnnotationLens { annotations { id } } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_point_layer_hands_its_lens_windows_to_the_renderer_as_row_filters(authenticated_context: HttpContext):
    """One COLUMN rule per window, inclusive on both ends -- the rule keeps exactly the rows whose anchors the lens itself keeps."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    table, system = await _seed_table_dataset(ctx, "points")
    await seed.register_into_scene(ctx, scene, system=system)
    window = await seed.create_windowed_lens(ctx, table, [{"axis": "x", "min": 2.0, "max": 5.0}])
    for value in range(8):
        await models.CoordinateAnchor.objects.acreate(table=table, organization=ctx.request.organization, coordinates={"x": value})

    result = await schema.execute(POINT_LAYER, context_value=ctx, variable_values={"input": {"scene": str(scene.pk), "lens": str(window.pk)}})
    assert not result.errors, result.errors
    layer = result.data["createPointLayer"]
    (rule,) = layer["windowFilters"]
    assert rule == {"kind": "COLUMN", "table": str(table.pk), "column": "x", "min": 2.0, "max": 5.0, "label": "x window"}
    assert layer["filterBys"] == [], "the window is the lens' selection, not a picker entry"

    column = np.arange(8, dtype=float)
    applied = set(np.flatnonzero((column >= rule["min"]) & (column <= rule["max"])).tolist())
    kept = {anchor.coordinates["x"] async for anchor in window.active_anchors}
    assert applied == kept == {2, 3, 4, 5}, "the rule the viewer runs and the test the server runs agree, both ends in"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_layer_hands_its_lens_windows_on_as_a_clip(authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    mesh = await _mesh_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=await sync_to_async(lambda: mesh.coordinate_system)())
    window = await seed.create_windowed_lens(ctx, mesh, [{"axis": "z", "min": 0.0, "max": 5.0}])
    whole = await seed.create_windowed_lens(ctx, mesh)

    clipped = await schema.execute(MESH_LAYER, context_value=ctx, variable_values={"input": {"scene": str(scene.pk), "lens": str(window.pk)}})
    assert not clipped.errors, clipped.errors
    assert clipped.data["createMeshLayer"]["clip"] == [{"axis": "z", "min": 0.0, "max": 5.0}]
    unclipped = await schema.execute(MESH_LAYER, context_value=ctx, variable_values={"input": {"scene": str(scene.pk), "lens": str(whole.pk)}})
    assert not unclipped.errors, unclipped.errors
    assert unclipped.data["createMeshLayer"]["clip"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_series_reads_through_its_lens_windows(authenticated_context: HttpContext):
    ctx = authenticated_context
    chart = await _chart(ctx)
    table = await _table(ctx, "measurements", _MEASUREMENTS)
    await _register(ctx, table["coordinateSystem"]["id"], chart["worldCoordinateSystem"]["id"], {"kind": "BY_DIMENSION", "parts": [{"kind": "AFFINE", "inputAxes": ["x"], "outputAxes": ["d"], "affine": [[0.001, 0.0]]}]})
    row = await models.TableDataset.objects.aget(pk=table["id"])
    window = await seed.create_windowed_lens(ctx, row, [{"axis": "x", "min": 100.0, "max": 400.0}])

    made = (await _ok(ctx, SERIES_WINDOWED, {"chart": chart["id"], "lens": str(window.pk)}))["createSeriesChartLayer"]
    assert made["windowFilters"] == [{"column": "x", "min": 100.0, "max": 400.0}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_annotation_lens_names_the_shapes_in_its_windows(authenticated_context: HttpContext):
    """A window is a value range in the drawing space: a point at y=10 is outside y 0..5, and a point at y=2 inside."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Drawn on", axes=seed.YX_AXES, shapes=[[64, 64]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    inside = await _draw(ctx, collection["id"], "POINT", [[2.0, 3.0]])
    outside = await _draw(ctx, collection["id"], "POINT", [[10.0, 3.0]])
    row = await models.AnnotationCollection.objects.aget(pk=collection["id"])
    window = await seed.create_windowed_lens(ctx, row, [{"axis": "y", "min": 0.0, "max": 5.0}])
    whole = await seed.create_windowed_lens(ctx, row)

    async def names(lens: models.Lens) -> set[str]:
        result = await schema.execute(ANNOTATIONS_OF, context_value=ctx, variable_values={"id": str(lens.pk)})
        assert not result.errors, result.errors
        return {annotation["id"] for annotation in result.data["lens"]["annotations"]}

    assert await names(window) == {inside["id"]}
    assert await names(whole) == {inside["id"], outside["id"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_range_over_the_layers_own_coordinate_column_is_a_window_and_not_a_picker_rule(authenticated_context: HttpContext):
    """A bounded rule on the point layer's own coordinate column would store a window twice; the lens is where a selection lives."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    table, system = await _seed_table_dataset(ctx, "points")
    await seed.register_into_scene(ctx, scene, system=system)
    whole = await seed.create_windowed_lens(ctx, table)

    refused = await schema.execute(
        "mutation C($input: CreatePointLayerInput!) { createPointLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(whole.pk), "filterBys": [{"table": str(table.pk), "column": "x", "min": 0.0, "max": 5.0}]}},
    )
    assert refused.errors and "createTableLens" in refused.errors[0].message, refused.errors
    allowed = await schema.execute(
        "mutation C($input: CreatePointLayerInput!) { createPointLayer(input: $input) { id filterBys { column } } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(whole.pk), "filterBys": [{"table": str(table.pk), "column": "photons", "min": 100.0}]}},
    )
    assert not allowed.errors, allowed.errors
    assert allowed.data["createPointLayer"]["filterBys"] == [{"column": "photons"}], "a measurement is a picker's to filter by"
