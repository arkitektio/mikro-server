"""A BY_DIMENSION edge on the walk to intrinsic 500'd every draw into the collection behind it.

`to_matrix` composes at one fixed rank and has no case for BY_DIMENSION -- deliberately, and
the reason is not laziness: a BY_DIMENSION edge leaves an output axis it never mentions
*unconstrained*, and a homogeneous matrix has no way to write "absent" that is not a zero. So
the box it would produce cannot exist in the positional shape `intrinsic_bbox` / `bbox_cube`
are stored in.

The bug was that `path_to_intrinsic` walked across such an edge anyway and let the resulting
`NonAffineTransformError` out through `createAnnotation`. The sibling hole is the same walk's
`dataset is None` branch, which said "when I belong to no dataset, any edge will do" -- so an
annotation collection's drawing space crossed its *registration* into a scene's world, exactly
what that function's docstring says has no business in the chain.

Both are fixed by stopping the walk, and by `intrinsic_frame` stopping in the same place: a box
that stays in the frame it was drawn in must be *labelled* with that frame.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


CREATE_COLLECTION = """
mutation M($input: CreateAnnotationCollectionInput!) {
  createAnnotationCollection(input: $input) { id coordinateSystem { id } }
}
"""

CREATE = """
mutation Create($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id intrinsicBbox { min max } }
}
"""

CREATE_MANY = """
mutation CreateMany($input: CreateAnnotationsInput!) {
  createAnnotations(input: $input) { id intrinsicBbox { min max } }
}
"""


async def _collection_over(ctx: HttpContext, dataset, *, name: str) -> str:
    """A (y, x) drawing space over a (c, y, x) dataset, joined by the edge that states the rank change."""
    result = await schema.execute(
        CREATE_COLLECTION,
        context_value=ctx,
        variable_values={
            "input": {
                "name": name,
                "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                "derivedFrom": [
                    {
                        "kind": "DATASET",
                        "dataset": str(dataset.pk),
                        # The ordinary shape of a rank-changing derivation: the shapes were
                        # drawn on the y/x plane and say nothing about which channel.
                        "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]},
                    }
                ],
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createAnnotationCollection"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_drawing_behind_a_by_dimension_edge_succeeds(db, authenticated_context: HttpContext):
    """The draw goes through, and the box comes back in the frame it was drawn in."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume")
    collection_id = await _collection_over(ctx, dataset, name="Traced")

    result = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "POINT", "vectors": [[10.0, 20.0]]}},
    )
    assert not result.errors, result.errors

    # Two numbers, not three: the box is in the collection's own (y, x) space. Crossing the
    # edge would have had to invent a c extent, which is the claim the edge refuses to make.
    box = result.data["createAnnotation"]["intrinsicBbox"]
    assert box["min"] == [9.5, 19.5] and box["max"] == [10.5, 20.5], box


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_frame_named_is_the_frame_the_box_is_in(db, authenticated_context: HttpContext):
    """`bbox_system` and the chain must stop at the same system, or the numbers are mislabelled.

    Two halves of one fact: `effective_bbox_system` is what `nearestAnnotations` and
    `AnnotationFilter.intersects` compare within, so a frame naming a system the box was never
    pushed into silently compares boxes from different spaces.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume")
    collection_id = await _collection_over(ctx, dataset, name="Traced")

    def frames():
        collection = models.AnnotationCollection.objects.get(pk=collection_id)
        system = collection.coordinate_system
        return collection.effective_bbox_system.pk, graph_logic.intrinsic_frame(system).pk, system.pk, len(graph_logic.intrinsic_chain(system))

    named, walked, own, hops = await sync_to_async(frames)()
    assert hops == 0, "a BY_DIMENSION edge is not composable at fixed rank, so the walk stops before it"
    assert walked == own, "the walk stops where the box stops"
    assert named == own, "and the collection says so"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_composable_derivation_is_still_crossed_and_still_labelled(db, authenticated_context: HttpContext):
    """The truncation is about composability, not about being a collection.

    A SCALE derivation has a matrix, so the box *is* pushed into the dataset's pixel grid --
    and the collection must name that grid as the frame, which the explicit create path never
    did. Before, the boxes were denominated in the dataset's pixels and labelled as the
    collection's own space.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume")

    created = await schema.execute(
        CREATE_COLLECTION,
        context_value=ctx,
        variable_values={
            "input": {
                "name": "Halved",
                "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                "derivedFrom": [{"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "SCALE", "scale": [1.0, 2.0, 2.0]}}],
            }
        },
    )
    assert not created.errors, created.errors
    collection_id = created.data["createAnnotationCollection"]["id"]

    drawn = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "POINT", "vectors": [[0.0, 10.0, 20.0]]}},
    )
    assert not drawn.errors, drawn.errors
    box = drawn.data["createAnnotation"]["intrinsicBbox"]
    assert box["max"] == [0.5, 21.0, 41.0], box

    def frame():
        collection = models.AnnotationCollection.objects.get(pk=collection_id)
        return collection.effective_bbox_system.pk, dataset.coordinate_system.pk, collection.coordinate_system.pk

    named, intrinsic, own = await sync_to_async(frame)()
    assert named == intrinsic and named != own, "the boxes are in the dataset's pixels, and the collection must name it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_bulk_draw_agrees_with_the_single_draw(db, authenticated_context: HttpContext):
    """`createAnnotations` resolves the chain separately, so it can disagree separately."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume")
    collection_id = await _collection_over(ctx, dataset, name="Batched")

    one = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "POINT", "vectors": [[10.0, 20.0]]}},
    )
    assert not one.errors, one.errors

    many = await schema.execute(
        CREATE_MANY,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "annotations": [{"kind": "POINT", "vectors": [[10.0, 20.0]]}]}},
    )
    assert not many.errors, many.errors
    assert many.data["createAnnotations"][0]["intrinsicBbox"] == one.data["createAnnotation"]["intrinsicBbox"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_edit_agrees_with_the_draw(db, authenticated_context: HttpContext):
    """`updateAnnotation` re-derives the box on new vectors, down the same walk."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume")
    collection_id = await _collection_over(ctx, dataset, name="Edited")

    drawn = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"collection": collection_id, "kind": "POINT", "vectors": [[1.0, 1.0]]}},
    )
    assert not drawn.errors, drawn.errors

    edited = await schema.execute(
        "mutation U($input: UpdateAnnotationInput!) { updateAnnotation(input: $input) { id intrinsicBbox { min max } } }",
        context_value=ctx,
        variable_values={"input": {"id": drawn.data["createAnnotation"]["id"], "vectors": [[10.0, 20.0]]}},
    )
    assert not edited.errors, edited.errors
    box = edited.data["updateAnnotation"]["intrinsicBbox"]
    assert box["min"] == [9.5, 19.5] and box["max"] == [10.5, 20.5], box


#: One set of parameters per kind, enough for `to_matrix` to have something to read. MAP_AXIS
#: carries an `affine` here because `_edge_params` synthesizes one from its axis columns
#: before `to_matrix` ever sees it.
_PARAMS_FOR_KIND = {
    enums.TransformKindChoices.IDENTITY.value: {},
    enums.TransformKindChoices.SCALE.value: {"scale": [2.0, 2.0]},
    enums.TransformKindChoices.TRANSLATION.value: {"translation": [1.0, 1.0]},
    enums.TransformKindChoices.MAP_AXIS.value: {"affine": [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]},
    enums.TransformKindChoices.AFFINE.value: {"affine": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
    enums.TransformKindChoices.ROTATION.value: {"affine": [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0]]},
    enums.TransformKindChoices.SEQUENCE.value: {"scale": [2.0, 2.0], "translation": [1.0, 1.0]},
    enums.TransformKindChoices.BY_DIMENSION.value: {},
    enums.TransformKindChoices.FIELD.value: {},
    enums.TransformKindChoices.UNMAPPABLE.value: {},
}


def test_has_matrix_answers_for_every_kind_what_to_matrix_does():
    """The walk asks `has_matrix` and the composer asks `to_matrix`; they must agree on every kind.

    A new kind gets a branch in one and an entry in the other, and the gap between those two
    commits is a walk that crosses an edge it cannot compose -- which is this whole bug. The
    kinds are enumerated from the enum, so a kind added without a decision fails here.
    """
    assert set(_PARAMS_FOR_KIND) == {kind.value for kind in enums.TransformKindChoices}, "every kind needs a verdict"

    for kind, params in _PARAMS_FOR_KIND.items():
        try:
            coords_logic.to_matrix(kind, params, 2)
        except coords_logic.NonAffineTransformError:
            composable = False
        else:
            composable = True
        assert coords_logic.has_matrix(kind) is composable, f"{kind}: has_matrix says {coords_logic.has_matrix(kind)}, to_matrix says {composable}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_collections_walk_does_not_cross_its_registration(db, authenticated_context: HttpContext):
    """A drawing space belongs to no dataset, and that must not mean "any edge will do".

    The scene-minted collection's only edge is its registration into the world. The walk used
    to take it -- appending a BY_DIMENSION edge to the chain -- and get away with it only
    because the world happened to dead-end one hop later, whereupon the whole chain was thrown
    away. A world with an edge of its own turns that accident into the same 500.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")

    drawn = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}},
    )
    assert not drawn.errors, drawn.errors

    def walk():
        collection = models.AnnotationCollection.objects.get(scene=scene)
        system = collection.coordinate_system
        registration = models.Transformation.objects.get(parent__isnull=True, input=system)
        return graph_logic.intrinsic_chain(system), graph_logic.intrinsic_frame(system).pk, system.pk, registration.kind, graph_logic.transform_version(system)

    chain, walked, own, kind, version = await sync_to_async(walk)()
    assert kind == enums.TransformKindChoices.BY_DIMENSION.value, "the registration is the edge in question"
    assert chain == [], "a registration is a fact about a scene, not about the dataset's pixel geometry"
    assert walked == own, "so the frame is the drawing space itself"
    assert version == 0, "and nothing on the chain has a version to sum, because the chain is empty"
