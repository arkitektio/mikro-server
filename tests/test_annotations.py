"""Tests for annotations and their collections.

The scene path of ``createAnnotation`` is the load-bearing sugar: the first
shape drawn on a scene mints the scene's collection -- its own coordinate
system copying the world's axes, a VALIDATED identity registration into the
world, and one annotation layer -- and every later shape appends to all of it.
The collection path stays inert: appending never touches layers or edges.
"""

import pytest
from asgiref.sync import sync_to_async

from core import enums, models
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed


CREATE = """
mutation Create($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) {
    id
    name
    kind
    vectors
    strokeWidth
    filled
    createdWithTransforms
    intrinsicBbox { min max }
    collection { id name scene { id } coordinateSystem { id residents { __typename } } }
  }
}
"""


def _counts(scene: "models.Scene") -> dict:
    return {
        "collections": models.AnnotationCollection.objects.count(),
        "systems": models.CoordinateSystem.objects.filter(annotation_collections__isnull=False).count(),
        "layers": models.Layer.objects.filter(scene=scene, kind=enums.LayerKindChoices.ANNOTATION.value).count(),
        "registrations": models.Transformation.objects.filter(parent__isnull=True, output=scene.world).count(),
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_drawing_on_a_scene_mints_its_collection(db, authenticated_context: HttpContext):
    """The first annotation mints collection + system + registration + layer; the second reuses them all."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")

    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}},
    )
    assert not result.errors, result.errors
    first = result.data["createAnnotation"]
    assert first["collection"]["scene"]["id"] == str(scene.id)
    assert [r["__typename"] for r in first["collection"]["coordinateSystem"]["residents"]] == ["AnnotationCollection"], "the shapes live in the collection's own space"
    assert first["strokeWidth"] == 1.0

    counts = await sync_to_async(_counts)(scene)
    assert counts == {"collections": 1, "systems": 1, "layers": 1, "registrations": 1}

    def registration():
        edge = models.Transformation.objects.get(parent__isnull=True, output=scene.world)
        system = models.CoordinateSystem.objects.get(annotation_collections__isnull=False)
        world_axes = [axis.name for axis in scene.world.axes.all().order_by("order")]
        drawing_axes = [axis.name for axis in system.axes.all().order_by("order")]
        return edge, world_axes, drawing_axes

    edge, world_axes, drawing_axes = await sync_to_async(registration)()
    assert drawing_axes == world_axes, "the drawing space mirrors the world's axes"
    assert edge.kind == enums.TransformKindChoices.BY_DIMENSION.value
    assert edge.validity == enums.PlacementValidityChoices.VALIDATED.value, "an identity between two spaces with the same axes is exact by construction"

    # The second shape appends: nothing new is minted.
    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[4.0, 5.0, 6.0]]}},
    )
    assert not result.errors, result.errors
    second = result.data["createAnnotation"]
    assert second["collection"]["id"] == first["collection"]["id"]

    counts = await sync_to_async(_counts)(scene)
    assert counts == {"collections": 1, "systems": 1, "layers": 1, "registrations": 1}
    assert await models.Annotation.objects.acount() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_chain_version_is_readable_so_staleness_is_detectable(db, authenticated_context: HttpContext):
    """`createdWithTransforms` finally has something to be compared with.

    The stored number always recorded the chain an annotation was drawn against, and
    `updateTransformation` always wrote a history row -- but the *current* chain version
    was exposed nowhere, so no client could tell a fresh shape from one whose chain has moved
    underneath it. `CoordinateSystem.transformVersion` is the missing read half, and it counts
    those history rows rather than a column any writer has to remember to bump.

    Both halves are provenance: neither takes part in resolving a coordinate, and a
    refinement does not move the shape's stored vectors.

    **The edge refined here is the derivation, and that is the point.** This used to refine a
    scene registration and expect the number to move, which the field's own description
    forbids -- it is the chain "down to its dataset's intrinsic pixel space", and a
    registration points the other way. The old walk counted it anyway while the box was
    composed along a different (empty) chain, so the number reported drift in a map the
    stored geometry never depended on. What a registration moving changes is where the
    collection sits in a world, which is a fact about its placement and is read off the
    registration's own provenance.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Drift")

    collection = await schema.execute(
        "mutation M($input: CreateAnnotationCollectionInput!) { createAnnotationCollection(input: $input) { id coordinateSystem { id } } }",
        context_value=ctx,
        variable_values={
            "input": {
                "name": "Traced",
                "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                "derivedFrom": [{"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "SCALE", "scale": [1.0, 2.0, 2.0]}}],
            }
        },
    )
    assert not collection.errors, collection.errors
    collection_id = collection.data["createAnnotationCollection"]["id"]
    system_id = collection.data["createAnnotationCollection"]["coordinateSystem"]["id"]

    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "POINT", "vectors": [[0.0, 2.0, 3.0]]}},
    )
    assert not result.errors, result.errors
    drawn_against = result.data["createAnnotation"]["createdWithTransforms"]
    assert drawn_against, "the box was pushed down a real chain, so there is a version to record"

    current = """query V($id: ID!) { coordinateSystem(id: $id) { transformVersion } }"""
    before = await schema.execute(current, context_value=ctx, variable_values={"id": system_id})
    assert not before.errors, before.errors
    assert before.data["coordinateSystem"]["transformVersion"] == drawn_against, "nothing has moved, so the shape is not stale"

    edge = await sync_to_async(models.Transformation.objects.get)(parent__isnull=True, input_id=int(system_id))
    refined = await schema.execute(
        "mutation R($input: UpdateTransformationInput!) { updateTransformation(input: $input) { id version } }",
        context_value=ctx,
        variable_values={"input": {"id": str(edge.pk), "validity": "VALIDATED"}},
    )
    assert not refined.errors, refined.errors

    after = await schema.execute(current, context_value=ctx, variable_values={"id": system_id})
    assert not after.errors, after.errors
    assert after.data["coordinateSystem"]["transformVersion"] != drawn_against, "a refined derivation leaves the shape detectably stale"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_drawing_space_has_no_chain_to_be_stale_against(db, authenticated_context: HttpContext):
    """The scene-minted collection answers 0, because its boxes are in its own coordinates.

    Its only edge is the registration into the world, and a registration is not on the path
    to pixels. So there is nothing to sum and nothing to go stale: the stored box does not
    depend on that edge, and the two zeros agreeing is the truth rather than a coincidence.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")

    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}},
    )
    assert not result.errors, result.errors
    assert result.data["createAnnotation"]["createdWithTransforms"] == 0
    system_id = result.data["createAnnotation"]["collection"]["coordinateSystem"]["id"]

    edge = await sync_to_async(models.Transformation.objects.get)(parent__isnull=True, output=scene.world)
    refined = await schema.execute(
        "mutation R($input: UpdateTransformationInput!) { updateTransformation(input: $input) { id version } }",
        context_value=ctx,
        variable_values={"input": {"id": str(edge.pk), "validity": "VALIDATED"}},
    )
    assert not refined.errors, refined.errors
    assert refined.data["updateTransformation"]["version"] > 1, "the placement moved, and the edge's own version says so"

    after = await schema.execute(
        "query V($id: ID!) { coordinateSystem(id: $id) { transformVersion } }",
        context_value=ctx,
        variable_values={"id": system_id},
    )
    assert not after.errors, after.errors
    assert after.data["coordinateSystem"]["transformVersion"] == 0, "the shape's own geometry never crossed that edge"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_chain_version_of_a_space_with_no_pixels_is_zero(db, authenticated_context: HttpContext):
    """A shared world has no path down to a pixel grid, and answers 0 rather than failing.

    The field sits on every coordinate system, so a client will ask a world for it. A
    calibrated or shared space's edges point *away* from intrinsic (see
    `graph_logic.intrinsic_chain`), so there is no chain to sum -- and no staleness to
    detect either, because nothing is denominated in its pixels.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Worldly")
    world_id = await sync_to_async(lambda: str(scene.world.pk))()

    result = await schema.execute(
        "query V($id: ID!) { coordinateSystem(id: $id) { residents { __typename } transformVersion } }",
        context_value=ctx,
        variable_values={"id": world_id},
    )
    assert not result.errors, result.errors
    assert result.data["coordinateSystem"]["residents"] == [], "a world holds nothing"
    assert result.data["coordinateSystem"]["transformVersion"] == 0, "no chain to sum, and the field must say so rather than raise"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_collection_xor_scene(db, authenticated_context: HttpContext):
    """Exactly one of collection/scene: both or neither is an authoring error."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    neither = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert neither.errors, "neither collection nor scene must be refused"

    first = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert not first.errors, first.errors
    collection_id = first.data["createAnnotation"]["collection"]["id"]

    both = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "collection": collection_id, "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert both.errors, "collection and scene together must be refused"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_drawing_into_a_collection_appends_only(db, authenticated_context: HttpContext):
    """The collection path never touches layers or edges."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    first = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert not first.errors, first.errors
    collection_id = first.data["createAnnotation"]["collection"]["id"]
    counts_before = await sync_to_async(_counts)(scene)

    appended = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "PATH", "vectors": [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]}},
    )
    assert not appended.errors, appended.errors
    assert appended.data["createAnnotation"]["collection"]["id"] == collection_id
    assert await sync_to_async(_counts)(scene) == counts_before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_annotation_layer_in_a_second_scene_needs_a_registration(db, authenticated_context: HttpContext):
    """A foreign scene refuses the layer as UNREGISTERED until the registration is authored."""
    ctx = authenticated_context
    scene_a = await seed.create_scene(ctx, "Home")
    scene_b = await seed.create_scene(ctx, "Away")

    first = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene_a.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert not first.errors, first.errors
    collection_id = first.data["createAnnotation"]["collection"]["id"]

    layer_mutation = "mutation M($i: CreateAnnotationLayerInput!) { createAnnotationLayer(input: $i) { id } }"
    refused = await schema.execute(
        layer_mutation,
        context_value=ctx,
        variable_values={"i": {"scene": str(scene_b.id), "annotationCollection": collection_id}},
    )
    assert refused.errors, "an unregistered collection must be refused in a foreign scene"
    assert "createTransformation" in str(refused.errors[0])

    def collection_system():
        return models.AnnotationCollection.objects.get(pk=collection_id).coordinate_system

    system = await sync_to_async(collection_system)()
    await seed.register_into_scene(ctx, scene_b, system=system)

    allowed = await schema.execute(
        layer_mutation,
        context_value=ctx,
        variable_values={"i": {"scene": str(scene_b.id), "annotationCollection": collection_id}},
    )
    assert not allowed.errors, allowed.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_annotation_recomputes_derived_fields(db, authenticated_context: HttpContext):
    """New vectors re-derive the bounding box; the rest edits in place."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    made = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}},
    )
    assert not made.errors, made.errors
    annotation = made.data["createAnnotation"]
    bbox_before = annotation["intrinsicBbox"]

    update = """
    mutation Update($input: UpdateAnnotationInput!) {
      updateAnnotation(input: $input) {
        id
        name
        filled
        vectors
        intrinsicBbox { min max }
      }
    }
    """
    result = await schema.execute(
        update,
        context_value=ctx,
        variable_values={"input": {"id": annotation["id"], "name": "Renamed", "filled": True, "vectors": [[10.0, 20.0, 30.0]]}},
    )
    assert not result.errors, result.errors
    updated = result.data["updateAnnotation"]
    assert updated["name"] == "Renamed"
    assert updated["filled"] is True
    assert updated["vectors"] == [[10.0, 20.0, 30.0]]
    assert updated["intrinsicBbox"] != bbox_before, "moved vectors must move the derived box"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_guards(db, authenticated_context: HttpContext, bot_context: HttpContext):
    """Only the creator (or an admin) deletes; a same-org bot without ownership is denied."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    made = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert not made.errors, made.errors
    annotation = made.data["createAnnotation"]
    collection_id = annotation["collection"]["id"]

    delete_annotation = "mutation D($input: DeleteAnnotationInput!) { deleteAnnotation(input: $input) }"
    denied = await schema.execute(delete_annotation, context_value=bot_context, variable_values={"input": {"id": annotation["id"]}})
    assert denied.errors, "a non-owner non-admin must not delete an annotation"
    assert await models.Annotation.objects.filter(pk=annotation["id"]).aexists()

    delete_collection = "mutation D($input: DeleteAnnotationCollectionInput!) { deleteAnnotationCollection(input: $input) }"
    denied = await schema.execute(delete_collection, context_value=bot_context, variable_values={"input": {"id": collection_id}})
    assert denied.errors, "a non-owner non-admin must not delete a collection"

    allowed = await schema.execute(delete_annotation, context_value=ctx, variable_values={"input": {"id": annotation["id"]}})
    assert not allowed.errors, allowed.errors
    assert not await models.Annotation.objects.filter(pk=annotation["id"]).aexists()

    allowed = await schema.execute(delete_collection, context_value=ctx, variable_values={"input": {"id": collection_id}})
    assert not allowed.errors, allowed.errors
    assert not await models.AnnotationCollection.objects.filter(pk=collection_id).aexists()
    assert not await models.CoordinateSystem.objects.filter(annotation_collections__isnull=False).aexists(), "the drawing system cascades with its collection"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_coordinates_surface(db, authenticated_context: HttpContext):
    """Pins go in and come back as named coordinates; an update replaces the whole set."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    made = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "kind": "POINT",
                "vectors": [[0.0, 0.0, 0.0]],
                "coordinates": [{"name": "t", "value": 0}, {"name": "c", "value": 2}],
            }
        },
    )
    assert not made.errors, made.errors
    annotation_id = made.data["createAnnotation"]["id"]

    query = "query Get($id: ID!) { annotation(id: $id) { coordinates { name value } } }"
    result = await schema.execute(query, context_value=ctx, variable_values={"id": annotation_id})
    assert not result.errors, result.errors
    pins = {c["name"]: c["value"] for c in result.data["annotation"]["coordinates"]}
    assert pins == {"t": 0, "c": 2}

    update = "mutation U($input: UpdateAnnotationInput!) { updateAnnotation(input: $input) { coordinates { name value } } }"
    result = await schema.execute(
        update,
        context_value=ctx,
        variable_values={"input": {"id": annotation_id, "coordinates": [{"name": "t", "value": 5}]}},
    )
    assert not result.errors, result.errors
    pins = {c["name"]: c["value"] for c in result.data["updateAnnotation"]["coordinates"]}
    assert pins == {"t": 5}, "the whole pin set is replaced, not merged"


async def _draw(ctx: HttpContext, target: dict, **fields) -> dict:
    """One shape via createAnnotation; returns the payload."""
    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {**target, "kind": "POINT", **fields}},
    )
    assert not result.errors, result.errors
    return result.data["createAnnotation"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pinned_to_filter(db, authenticated_context: HttpContext):
    """Containment on the pinned dict: spanning a coordinate never matches a pin on it."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    first = await _draw(ctx, {"scene": str(scene.id)}, name="both", vectors=[[0.0, 0.0, 0.0]], coordinates=[{"name": "t", "value": 0}, {"name": "c", "value": 0}])
    collection_id = first["collection"]["id"]
    target = {"collection": collection_id}
    await _draw(ctx, target, name="t3", vectors=[[1.0, 1.0, 1.0]], coordinates=[{"name": "t", "value": 3}])
    await _draw(ctx, target, name="spanning", vectors=[[2.0, 2.0, 2.0]])

    query = "query L($filters: AnnotationFilter) { annotations(filters: $filters) { name } }"

    result = await schema.execute(query, context_value=ctx, variable_values={"filters": {"pinnedTo": [{"name": "t", "value": 3}]}})
    assert not result.errors, result.errors
    assert {a["name"] for a in result.data["annotations"]} == {"t3"}

    result = await schema.execute(query, context_value=ctx, variable_values={"filters": {"pinnedTo": [{"name": "t", "value": 0}, {"name": "c", "value": 0}]}})
    assert not result.errors, result.errors
    assert {a["name"] for a in result.data["annotations"]} == {"both"}

    result = await schema.execute(query, context_value=ctx, variable_values={"filters": {"pinnedTo": [{"name": "z", "value": 0}]}})
    assert not result.errors, result.errors
    assert result.data["annotations"] == [], "a spanning annotation never matches a pin"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_spatial_filters(db, authenticated_context: HttpContext):
    """Overlap and point containment against the cube column, scoped to one frame."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    first = await _draw(ctx, {"scene": str(scene.id)}, name="near", vectors=[[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]])
    collection_id = first["collection"]["id"]
    await _draw(ctx, {"collection": collection_id}, name="far", vectors=[[100.0, 100.0, 100.0], [200.0, 200.0, 200.0]])

    query = "query L($filters: AnnotationFilter) { annotations(filters: $filters) { name } }"

    result = await schema.execute(
        query,
        context_value=ctx,
        variable_values={"filters": {"collection": collection_id, "intersects": {"min": [5.0, 5.0, 5.0], "max": [20.0, 20.0, 20.0]}}},
    )
    assert not result.errors, result.errors
    assert {a["name"] for a in result.data["annotations"]} == {"near"}

    result = await schema.execute(
        query,
        context_value=ctx,
        variable_values={"filters": {"collection": collection_id, "containsPoint": [150.0, 150.0, 150.0]}},
    )
    assert not result.errors, result.errors
    assert {a["name"] for a in result.data["annotations"]} == {"far"}

    result = await schema.execute(
        query,
        context_value=ctx,
        variable_values={"filters": {"intersects": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}}},
    )
    assert result.errors, "a spatial predicate without a frame must be refused"
    assert "frame" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nearest_annotations(db, authenticated_context: HttpContext):
    """Cube-distance ordering, limit, and exclusion of shapes with no box."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    first = await _draw(ctx, {"scene": str(scene.id)}, name="a", vectors=[[0.0, 0.0, 0.0]])
    collection_id = first["collection"]["id"]
    await _draw(ctx, {"collection": collection_id}, name="b", vectors=[[50.0, 50.0, 50.0]])
    await _draw(ctx, {"collection": collection_id}, name="c", vectors=[[200.0, 200.0, 200.0]])
    await _draw(ctx, {"collection": collection_id}, name="boxless", vectors=[])

    query = "query N($collection: ID!, $point: [Float!]!, $limit: Int!) { nearestAnnotations(collection: $collection, point: $point, limit: $limit) { name } }"

    result = await schema.execute(query, context_value=ctx, variable_values={"collection": collection_id, "point": [1.0, 1.0, 1.0], "limit": 10})
    assert not result.errors, result.errors
    assert [a["name"] for a in result.data["nearestAnnotations"]] == ["a", "b", "c"], "ordered by cube distance; the boxless shape is nowhere, not near"

    result = await schema.execute(query, context_value=ctx, variable_values={"collection": collection_id, "point": [199.0, 199.0, 199.0], "limit": 2})
    assert not result.errors, result.errors
    assert [a["name"] for a in result.data["nearestAnnotations"]] == ["c", "b"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bulk_create(db, authenticated_context: HttpContext):
    """Many shapes, one call: one minted bundle, per-shape boxes, uniform version, history intact."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    mutation = """
    mutation Bulk($input: CreateAnnotationsInput!) {
      createAnnotations(input: $input) {
        id
        name
        createdWithTransforms
        intrinsicBbox { min max }
        collection { id }
      }
    }
    """
    specs = [
        {"kind": "POINT", "name": "one", "vectors": [[0.0, 0.0, 0.0]], "coordinates": [{"name": "t", "value": 0}]},
        {"kind": "POINT", "name": "two", "vectors": [[5.0, 5.0, 5.0]]},
        {"kind": "PATH", "name": "three", "vectors": [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]},
    ]
    result = await schema.execute(
        mutation,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "annotations": specs}},
    )
    assert not result.errors, result.errors
    created = result.data["createAnnotations"]
    assert [a["name"] for a in created] == ["one", "two", "three"]
    assert len({a["collection"]["id"] for a in created}) == 1
    assert all(a["intrinsicBbox"] is not None for a in created), "every shape gets its box"
    assert len({a["createdWithTransforms"] for a in created}) == 1, "one version read for the whole batch"

    counts = await sync_to_async(_counts)(scene)
    assert counts == {"collections": 1, "systems": 1, "layers": 1, "registrations": 1}, "the batch mints the scene bundle exactly once"
    assert await models.Annotation.objects.acount() == 3

    def history_rows():
        return models.Annotation.provenance.model.objects.count()

    assert await sync_to_async(history_rows)() == 3, "bulk-created rows still write their history"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_annotation_lists_are_org_scoped(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """The root lists never leak across organizations -- the hole the old dataRois field had."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx)

    made = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[0.0, 0.0, 0.0]]}},
    )
    assert not made.errors, made.errors

    query = "query { annotations { id } annotationCollections { id } }"
    ours = await schema.execute(query, context_value=ctx)
    assert not ours.errors, ours.errors
    assert len(ours.data["annotations"]) == 1
    assert len(ours.data["annotationCollections"]) == 1

    theirs = await schema.execute(query, context_value=other_org_context)
    assert not theirs.errors, theirs.errors
    assert theirs.data["annotations"] == []
    assert theirs.data["annotationCollections"] == []
