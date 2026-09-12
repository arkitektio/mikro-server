"""Tests for the polymorphic layer subtypes backed by non-array sources.

Each subtype (AnnotationLayer, PointLayer, TrackLayer, MeshLayer) is a concrete
implementation of the ``Layer`` interface, placed in a scene and returned
heterogeneously through ``Scene.layers``. Every source must already have a
path to the scene's world -- a layer mutation checks the placement, it never
writes one -- so each seed helper hands back the coordinate system to register.
"""

import pytest

from asgiref.sync import sync_to_async
from core import models, enums
from core.logic import graph as graph_logic
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed


async def _seed_scene(ctx: HttpContext) -> models.Scene:
    return await seed.create_scene(ctx)


def _seed_table_dataset_sync(ctx: HttpContext, key: str, *, with_track: bool = False) -> models.TableDataset:
    """A table dataset with declared coordinate columns -- the only table a layer draws from.

    The dataset is the whole mapping: coordinate columns are its axes, its own system is
    the space, and the roles are the identities. No layer binds a column by name.
    """
    store = models.ParquetStore.objects.create(path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization, populated=True)
    dataset = models.TableDataset.objects.create(name=key, store=store, organization=ctx.request.organization)
    columns = [
        {"name": "y", "role": enums.ColumnRoleChoices.COORDINATE.value, "axis_type": enums.AxisTypeChoices.SPACE.value},
        {"name": "x", "role": enums.ColumnRoleChoices.COORDINATE.value, "axis_type": enums.AxisTypeChoices.SPACE.value},
        {"name": "photons", "role": enums.ColumnRoleChoices.ATTRIBUTE.value, "axis_type": None},
    ]
    if with_track:
        columns.append({"name": "track", "role": enums.ColumnRoleChoices.TRACK_ID.value, "axis_type": None})
    for order, column in enumerate(columns):
        models.Column.objects.create(table=dataset, order=order, dtype="DOUBLE", **column)
    graph_logic.create_collection_system(
        name=f"{key}/table",
        axes=[seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        owner=dataset,
        ctx=seed._creation(ctx),
    )
    return dataset


async def _seed_table_dataset(ctx: HttpContext, key: str, *, with_track: bool = False) -> tuple[models.TableDataset, models.CoordinateSystem]:
    dataset = await sync_to_async(_seed_table_dataset_sync)(ctx, key, with_track=with_track)
    system = await sync_to_async(lambda: dataset.coordinate_system)()
    return dataset, system


def _seed_mesh_collection_sync(ctx: HttpContext) -> models.MeshCollection:
    collection = models.MeshCollection.objects.create(
        version="v1",
        spec_version="fabriks/1",
        store=seed._seed_fabriks_store_sync(ctx, axes=None, populated=True),
        organization=ctx.request.organization,
    )
    graph_logic.create_collection_system(
        name=f"{collection.version}/mesh",
        axes=[seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        owner=collection,
        ctx=seed._creation(ctx),
    )
    return collection


async def _seed_mesh_collection(ctx: HttpContext) -> tuple[models.MeshCollection, models.CoordinateSystem]:
    collection = await sync_to_async(_seed_mesh_collection_sync)(ctx)
    system = await sync_to_async(lambda: collection.coordinate_system)()
    return collection, system


def _seed_annotation_collection_sync(ctx: HttpContext) -> models.AnnotationCollection:
    # An annotation lives in its collection's own system, not "on a dataset" or a
    # scene: that is what lets it outlive the scene it happened to be viewed in.
    collection = models.AnnotationCollection.objects.create(
        name="Drawn",
        organization=ctx.request.organization,
        creator=ctx.request.user,
    )
    graph_logic.create_collection_system(
        name=f"{collection.name}/drawing",
        axes=[seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)],
        owner=collection,
        ctx=seed._creation(ctx),
    )
    models.Annotation.objects.create(
        collection=collection,
        name="Shape",
        kind=enums.AnnotationKindChoices.POLYGON.value,
        vectors=[[0.0, 0.0], [0.0, 10.0], [10.0, 10.0]],
        creator=ctx.request.user,
    )
    return collection


async def _seed_annotation_collection(ctx: HttpContext) -> tuple[models.AnnotationCollection, models.CoordinateSystem]:
    collection = await sync_to_async(_seed_annotation_collection_sync)(ctx)
    system = await sync_to_async(lambda: collection.coordinate_system)()
    return collection, system


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_annotation_layer(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    collection, system = await _seed_annotation_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=system)

    mutation = """
        mutation Create($input: CreateAnnotationLayerInput!) {
            createAnnotationLayer(input: $input) {
                id
                __typename
                blending
                opacity
                annotationCollection { id annotations { id kind } }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "annotationCollection": str(collection.id)}},
    )
    assert not result.errors, result.errors
    data = result.data["createAnnotationLayer"]
    assert data["__typename"] == "AnnotationLayer"
    assert data["blending"] == "NORMAL"
    assert data["annotationCollection"]["id"] == str(collection.id)
    assert len(data["annotationCollection"]["annotations"]) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_annotation_layer_is_rejected(db, authenticated_context: HttpContext):
    """The collection's system has no path to this world, so the layer is refused with the fix named."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    collection, _ = await _seed_annotation_collection(ctx)

    result = await schema.execute(
        "mutation Create($input: CreateAnnotationLayerInput!) { createAnnotationLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "annotationCollection": str(collection.id)}},
    )
    assert result.errors, "an unplaced collection system must be refused"
    assert "createTransformation" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_annotation_layer_appears_in_scene_layers(db, authenticated_context: HttpContext):
    """An AnnotationLayer is returned polymorphically through Scene.layers."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    collection, system = await _seed_annotation_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=system)

    create = "mutation Create($input: CreateAnnotationLayerInput!) { createAnnotationLayer(input: $input) { id } }"
    result = await schema.execute(
        create,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "annotationCollection": str(collection.id)}},
    )
    assert not result.errors, result.errors

    query = """
        query Scene($id: ID!) {
            scene(id: $id) {
                layers {
                    __typename
                    id
                    ... on AnnotationLayer { annotationCollection { id } }
                }
            }
        }
    """
    result = await schema.execute(query, context_value=ctx, variable_values={"id": str(scene.id)})
    assert not result.errors, result.errors
    layers = result.data["scene"]["layers"]
    assert len(layers) == 1
    assert layers[0]["__typename"] == "AnnotationLayer"
    assert layers[0]["annotationCollection"]["id"] == str(collection.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_point_layer(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "points")
    await seed.register_into_scene(ctx, scene, system=system)

    mutation = """
        mutation Create($input: CreatePointLayerInput!) {
            createPointLayer(input: $input) {
                id
                __typename
                blending
                pointSize
                xColumn
                yColumn
                colormap
                tableDataset { id }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "tableDataset": str(dataset.id), "pointSize": 5.0}},
    )
    assert not result.errors, result.errors
    data = result.data["createPointLayer"]
    assert data["__typename"] == "PointLayer"
    assert data["blending"] == "NORMAL"
    assert data["pointSize"] == 5.0
    assert data["xColumn"] == "x", "the coordinate columns come from the dataset's declared schema, never a per-layer binding"
    assert data["colormap"] == "VIRIDIS"
    assert data["tableDataset"]["id"] == str(dataset.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_track_layer_needs_a_track_id_column(db, authenticated_context: HttpContext):
    """The track identity is the dataset's TRACK_ID role: a table without one has no tracks to draw."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "trackless")
    await seed.register_into_scene(ctx, scene, system=system)

    result = await schema.execute(
        "mutation Create($input: CreateTrackLayerInput!) { createTrackLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "tableDataset": str(dataset.id)}},
    )
    assert result.errors, "no TRACK_ID column, no tracks"
    assert "TRACK_ID" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_track_layer(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "tracks", with_track=True)
    await seed.register_into_scene(ctx, scene, system=system)

    mutation = """
        mutation Create($input: CreateTrackLayerInput!) {
            createTrackLayer(input: $input) {
                id
                __typename
                trackIdColumn
                lineWidth
                tableDataset { id }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "tableDataset": str(dataset.id)}},
    )
    assert not result.errors, result.errors
    data = result.data["createTrackLayer"]
    assert data["__typename"] == "TrackLayer"
    assert data["trackIdColumn"] == "track", "the track identity is the dataset's TRACK_ID column, resolved by role"
    assert data["lineWidth"] == 1.0
    assert data["tableDataset"]["id"] == str(dataset.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_track_layer(db, authenticated_context: HttpContext):
    """A track layer's display choices are retunable after creation.

    Which columns provide the coordinates and the track identity is NOT among
    them -- the dataset declares those by role, so there is nothing per-layer to
    change and no input field to change it with.
    """
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "tracks-update", with_track=True)
    await seed.register_into_scene(ctx, scene, system=system)

    created = await schema.execute(
        "mutation Create($input: CreateTrackLayerInput!) { createTrackLayer(input: $input) { id lineWidth } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "tableDataset": str(dataset.id)}},
    )
    assert not created.errors, created.errors
    layer_id = created.data["createTrackLayer"]["id"]
    assert created.data["createTrackLayer"]["lineWidth"] == 1.0

    mutation = """
        mutation Update($input: UpdateTrackLayerInput!) {
            updateTrackLayer(input: $input) {
                id
                __typename
                lineWidth
                colorByColumn
                colormap
                opacity
                visible
                trackIdColumn
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={
            "input": {
                "id": layer_id,
                "lineWidth": 3.5,
                "colorByColumn": "photons",
                "colormap": "MAGMA",
                "opacity": 0.5,
                "visible": False,
            }
        },
    )
    assert not result.errors, result.errors
    data = result.data["updateTrackLayer"]
    assert data["__typename"] == "TrackLayer"
    assert data["lineWidth"] == 3.5
    assert data["colorByColumn"] == "photons"
    assert data["colormap"] == "MAGMA"
    assert data["opacity"] == 0.5
    assert data["visible"] is False
    # Derived from the dataset's roles, untouched by the patch.
    assert data["trackIdColumn"] == "track"

    # An omitted field leaves the stored value alone rather than nulling it.
    partial = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"id": layer_id, "lineWidth": 2.0}},
    )
    assert not partial.errors, partial.errors
    assert partial.data["updateTrackLayer"]["lineWidth"] == 2.0
    assert partial.data["updateTrackLayer"]["colorByColumn"] == "photons"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_track_layer_refuses_another_kind(db, authenticated_context: HttpContext):
    """A point layer has no line width to set, and says so rather than patching one on."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "points-not-tracks")
    await seed.register_into_scene(ctx, scene, system=system)

    created = await schema.execute(
        "mutation Create($input: CreatePointLayerInput!) { createPointLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "tableDataset": str(dataset.id)}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        "mutation Update($input: UpdateTrackLayerInput!) { updateTrackLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"id": created.data["createPointLayer"]["id"], "lineWidth": 2.0}},
    )
    assert result.errors, "a point layer is not a track layer"
    assert "not a track layer" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_mesh_layer(db, authenticated_context: HttpContext):
    """A mesh layer renders a mesh COLLECTION: the collection owns the space the check walks from."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    collection, system = await _seed_mesh_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=system)

    mutation = """
        mutation Create($input: CreateMeshLayerInput!) {
            createMeshLayer(input: $input) {
                id
                __typename
                wireframe
                materialColor
                blending
                collection { id }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "meshCollection": str(collection.id), "wireframe": True}},
    )
    assert not result.errors, result.errors
    data = result.data["createMeshLayer"]
    assert data["__typename"] == "MeshLayer"
    assert data["wireframe"] is True
    assert data["materialColor"] == [255, 255, 255, 255]
    assert data["blending"] == "NORMAL"
    assert data["collection"]["id"] == str(collection.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_mesh_layer_is_rejected(db, authenticated_context: HttpContext):
    """The collection's own system has no path to this world, so the layer is refused."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    collection, _ = await _seed_mesh_collection(ctx)

    result = await schema.execute(
        "mutation Create($input: CreateMeshLayerInput!) { createMeshLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "meshCollection": str(collection.id)}},
    )
    assert result.errors, "an unplaced collection must be refused"
    assert "createTransformation" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_heterogeneous_scene_layers(db, authenticated_context: HttpContext):
    """A single scene holding several layer kinds returns them all through the polymorphic interface."""
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    annotation_collection, annotation_system = await _seed_annotation_collection(ctx)
    table_dataset, table_system = await _seed_table_dataset(ctx, "hetero", with_track=True)
    collection, mesh_system = await _seed_mesh_collection(ctx)
    await seed.register_into_scene(ctx, scene, system=annotation_system)
    await seed.register_into_scene(ctx, scene, system=table_system)
    await seed.register_into_scene(ctx, scene, system=mesh_system)

    for mutation, variables in [
        ("mutation M($i: CreateAnnotationLayerInput!){ createAnnotationLayer(input:$i){ id } }", {"i": {"scene": str(scene.id), "annotationCollection": str(annotation_collection.id)}}),
        ("mutation M($i: CreatePointLayerInput!){ createPointLayer(input:$i){ id } }", {"i": {"scene": str(scene.id), "tableDataset": str(table_dataset.id)}}),
        ("mutation M($i: CreateTrackLayerInput!){ createTrackLayer(input:$i){ id } }", {"i": {"scene": str(scene.id), "tableDataset": str(table_dataset.id)}}),
        ("mutation M($i: CreateMeshLayerInput!){ createMeshLayer(input:$i){ id } }", {"i": {"scene": str(scene.id), "meshCollection": str(collection.id)}}),
    ]:
        made = await schema.execute(mutation, context_value=ctx, variable_values=variables)
        assert not made.errors, made.errors

    query = """
        query Scene($id: ID!) {
            scene(id: $id) {
                layers {
                    __typename
                    ... on AnnotationLayer { annotationCollection { id } }
                    ... on PointLayer { pointSize tableDataset { id } }
                    ... on TrackLayer { trackIdColumn tableDataset { id } }
                    ... on MeshLayer { wireframe collection { id } }
                }
            }
        }
    """
    result = await schema.execute(query, context_value=ctx, variable_values={"id": str(scene.id)})
    assert not result.errors, result.errors
    typenames = {layer["__typename"] for layer in result.data["scene"]["layers"]}
    assert typenames == {"AnnotationLayer", "PointLayer", "TrackLayer", "MeshLayer"}


#: Every non-lens layer mutation, as (mutation, how to build its source, how to name it in the
#: input). Written as data rather than as four near-identical tests because the gate is one
#: function and the point is that all four reach it -- a fifth source kind added without its
#: gate should show up as a missing row here, not as four tests nobody thought to copy.
_UNPLACED_SOURCES = [
    ("createAnnotationLayer", "CreateAnnotationLayerInput", _seed_annotation_collection, "annotationCollection"),
    ("createPointLayer", "CreatePointLayerInput", lambda ctx: _seed_table_dataset(ctx, "unplaced-points"), "tableDataset"),
    ("createTrackLayer", "CreateTrackLayerInput", lambda ctx: _seed_table_dataset(ctx, "unplaced-tracks", with_track=True), "tableDataset"),
    ("createMeshLayer", "CreateMeshLayerInput", _seed_mesh_collection, "meshCollection"),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_name,input_name,seed_source,field", _UNPLACED_SOURCES, ids=[row[0] for row in _UNPLACED_SOURCES])
async def test_every_source_kind_is_refused_when_nothing_places_it(db, authenticated_context: HttpContext, mutation_name, input_name, seed_source, field):
    """The gate is not the lens path's alone. Skip the registration and each of these refuses.

    Every other test in this file registers the source first, which is right -- they are about
    what a layer *is* -- but it meant the refusal was pinned only for `createLayer` and for a
    scene rebind. A mesh, point, track or annotation mutation losing its
    `assert_placeable_in` would have gone unnoticed here.

    The message points at the mutation that closes the gap, because that is what is actually
    missing: the source reaches nowhere, and nothing about it is impossible.
    """
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    source, _system = await seed_source(ctx)

    result = await schema.execute(
        f"mutation Create($input: {input_name}!) {{ {mutation_name}(input: $input) {{ id }} }}",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), field: str(source.id)}},
    )
    assert result.errors, f"{mutation_name} composed a layer the graph does not place"
    assert "createTransformation" in str(result.errors[0]), result.errors[0]
