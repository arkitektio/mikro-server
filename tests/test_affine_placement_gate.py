"""Adding a layer must guarantee an affine to world, not merely a route to it.

The gate on every layer mutation used to ask `is_traversable`, which refuses exactly one
kind -- UNMAPPABLE. A FIELD passes it and has no closed form: its map is the values of an
array. So a layer over data registered only through a warp field or a label mask was
*accepted*, reported a `pathToWorld`, and then made `asAffine` raise -- creation promising a
placement the renderer could not draw with. Reachability is not placement.

Two halves, and both are needed or the guarantee is nominal:

**Creation refuses it**, with its own message. The two that existed are actively wrong here:
"author the registration" sends someone after an edge that is already there, and "UNMAPPABLE"
denies a correspondence the FIELD asserts.

**The walk prefers a route that condenses.** `_bfs_tree` ranks by bottleneck validity and
then by hops, and invariance is deliberately not a key -- so a one-hop VALIDATED field beat a
two-hop affine chain, and the layer the gate had just accepted on the strength of that affine
chain reported the field. The preference is not a filter: a placement whose *only* route is a
field still reports it, which is what keeps `pathToWorld` answering for rows written before
the gate existed and for the ones written straight through the ORM.

Every test below is written so that removing the gate it pins makes it fail.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from mikro_server.schema import schema
from tests import seed


PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers {
      id
      placement
      placementInvariance
      pathToWorld { inverted transformation { id kind } }
      asAffine { matrix inputAxes outputAxes total }
    }
  }
}
"""

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id }
}
"""

MAKE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

CREATE_SYSTEM = """
mutation CreateSystem($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id }
}
"""

SCENE_FROM_SYSTEM = """
mutation FromSystem($input: CreateSceneFromCoordinateSystemInput!) {
  createSceneFromCoordinateSystem(input: $input) { id layers { id } }
}
"""


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity: the scene-graph memo lives on the context."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


async def _register(ctx: HttpContext, input_id, output_id, transform: dict, validity: str | None = None) -> str:
    payload: dict = {"input": str(input_id), "output": str(output_id), "transform": transform}
    if validity is not None:
        payload["validity"] = validity
    result = await schema.execute(CREATE_TRANSFORM, context_value=_fresh_request(ctx), variable_values={"input": payload})
    assert not result.errors, result.errors
    return str(result.data["createTransformation"]["id"])


async def _make_layer(ctx: HttpContext, scene_id, lens: models.Lens):
    return await schema.execute(
        MAKE_LAYER,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"scene": str(scene_id), "lens": str(lens.pk)}},
    )


async def _layers(ctx: HttpContext, scene_id) -> list[dict]:
    result = await schema.execute(PLACEMENT, context_value=_fresh_request(ctx), variable_values={"id": str(scene_id)})
    assert not result.errors, result.errors
    return result.data["scene"]["layers"]


async def _mask_field_into(ctx: HttpContext, world, validity: str | None = None):
    """A dataset whose pixels are a map into an index space, and that space registered into world.

    The shape a segmentation actually has: (y,x) is consumed by the mask's own values and `i`
    -- one number per object -- comes out. Nothing about that composes into a matrix, but the
    route to world exists, which is exactly the case the old gate waved through.
    """
    mask = await seed.create_array_dataset(ctx, "Mask", axes=seed.YX_AXES, shapes=[[64, 64]])
    objects = await seed.create_array_dataset(ctx, "Objects", axes=[seed.axis("i", enums.AxisType.INDEX)], shapes=[[128]])
    mask_system, object_system = await sync_to_async(lambda: (mask.intrinsic_coordinate_system, objects.intrinsic_coordinate_system))()

    field_id = await _register(ctx, mask_system.pk, object_system.pk, {"kind": "FIELD", "field": str(mask_system.pk), "inputAxes": ["y", "x"], "outputAxes": ["i"]}, validity=validity)
    await _register(ctx, object_system.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["i"], "outputAxes": ["z"]}, validity=validity)
    return mask, mask_system, field_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_reachable_only_through_a_field_is_refused(authenticated_context: HttpContext):
    """A route exists, it composes into nothing, and creation says exactly that.

    ABLATION: drop `require_affine=True` from `assert_placeable_in` and this layer is created,
    `pathToWorld` returns the field route, and `asAffine` raises -- the shape the gate exists
    to make impossible.
    """
    scene = await seed.create_scene(authenticated_context, "Composition")
    world = await sync_to_async(lambda: scene.world)()
    mask, _mask_system, field_id = await _mask_field_into(authenticated_context, world)
    lens = await seed.create_lens(authenticated_context, mask, slices=[])

    made = await _make_layer(authenticated_context, scene.pk, lens)
    assert made.errors, "a route with no closed form places nothing a renderer can draw"

    message = str(made.errors[0])
    assert "affinely" in message, message
    assert f"{field_id} (FIELD)" in message, ("the blocking edge is named, or the reader has nothing to go and fix", message)
    # The two older verdicts are both wrong here, and saying either would send the reader
    # somewhere there is nothing to do.
    assert "Author the registration" not in message, ("the registration exists", message)
    assert "UNMAPPABLE" not in message, ("a FIELD asserts a correspondence; it just has no matrix", message)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_same_source_is_accepted_once_an_affine_route_exists(authenticated_context: HttpContext):
    """The refusal is about the route, not about the data: author one that condenses and it draws."""
    scene = await seed.create_scene(authenticated_context, "Composition")
    world = await sync_to_async(lambda: scene.world)()
    mask, mask_system, _field_id = await _mask_field_into(authenticated_context, world)
    lens = await seed.create_lens(authenticated_context, mask, slices=[])

    refused = await _make_layer(authenticated_context, scene.pk, lens)
    assert refused.errors

    await _register(authenticated_context, mask_system.pk, world.pk, {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [2.0, 2.0]})

    made = await _make_layer(authenticated_context, scene.pk, lens)
    assert not made.errors, made.errors

    (layer,) = await _layers(authenticated_context, scene.pk)
    assert layer["asAffine"] is not None, "the gate accepted it, so the matrix must exist"
    assert layer["asAffine"]["matrix"], layer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_walk_prefers_an_affine_route_over_a_better_known_field(authenticated_context: HttpContext):
    """The field route is VALIDATED; the affine route is one hop shorter and only INFERRED.

    Which is the whole point: `_bfs_tree` maximises bottleneck validity *first* and only then
    minimises hops, and invariance is deliberately not a key -- so the field wins on the key
    that is read first. Without the affine-first pass the gate accepts this layer (an affine
    route exists) and `asAffine` then raises on the route the walk actually returned.

    ABLATION: delete the `require_affine=True` first pass in `SceneGraph.placement_path` and
    `asAffine` errors here, naming the FIELD.
    """
    scene = await seed.create_scene(authenticated_context, "Composition")
    world = await sync_to_async(lambda: scene.world)()

    # Two hops, VALIDATED throughout: the mask's pixels dereference into an object space,
    # which someone checked against the world.
    mask, mask_system, field_id = await _mask_field_into(authenticated_context, world, validity="VALIDATED")
    lens = await seed.create_lens(authenticated_context, mask, slices=[])

    # One hop, INFERRED: nobody checked this one, and it is the only one that composes.
    affine_id = await _register(
        authenticated_context,
        mask_system.pk,
        world.pk,
        {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [2.0, 2.0]},
        validity="INFERRED",
    )

    made = await _make_layer(authenticated_context, scene.pk, lens)
    assert not made.errors, made.errors

    (layer,) = await _layers(authenticated_context, scene.pk)
    reported = [step["transformation"]["id"] for step in layer["pathToWorld"]]
    assert field_id not in reported, ("the better-known route does not condense, so it is not the one reported", layer["pathToWorld"])
    assert reported == [affine_id], layer["pathToWorld"]
    assert layer["asAffine"]["matrix"], "the reported route condenses, which is the whole guarantee"
    assert layer["placementInvariance"] != "DIFFEOMORPHIC", layer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_only_placement_still_reports_its_route(authenticated_context: HttpContext):
    """A preference, not a filter. Rows the gate never saw keep the answer they had.

    Written through the ORM, which is how a layer predating the gate -- or one whose affine
    registration was deleted afterwards -- looks. `pathToWorld` still answers, `placement`
    still says PLACED, and `asAffine` still errors naming the edge, exactly as
    `core/types/array_dataset.py` documents. Narrowing the universe instead of preferring
    within it would have turned all three into nulls and called registered data unregistered.
    """
    scene = await seed.create_scene(authenticated_context, "Composition")
    world = await sync_to_async(lambda: scene.world)()
    mask, _mask_system, field_id = await _mask_field_into(authenticated_context, world)
    lens = await seed.create_lens(authenticated_context, mask, slices=[])

    await sync_to_async(models.Layer.objects.create)(kind=enums.LayerKindChoices.IMAGE.value, scene=scene, lens=lens)

    result = await schema.execute(PLACEMENT, context_value=_fresh_request(authenticated_context), variable_values={"id": str(scene.pk)})
    assert result.errors, "asAffine has no matrix to give and says so rather than returning null"
    assert f"transformation {field_id} (FIELD)" in str(result.errors[0]), result.errors[0]

    (layer,) = result.data["scene"]["layers"]
    assert layer["placement"] == "PLACED", "it is placed -- by a map that is not a matrix"
    assert layer["placementInvariance"] == "DIFFEOMORPHIC", layer
    assert "FIELD" in [step["transformation"]["kind"] for step in layer["pathToWorld"]], layer["pathToWorld"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_scene_builder_refuses_by_default_and_skips_when_asked(authenticated_context: HttpContext):
    """One source the world does not place: the build fails, unless the caller has said to skip it.

    Fail is the default because a scene silently missing a layer is harder to notice than a
    refusal naming the source, and because the builder holding a different line from
    `createLayer` would be two rules for one question. `skipUnplaceable` is the opt-out, and
    it is the same choice `_is_renderable` and a coordinate-less table already make.
    """
    hub = await schema.execute(
        CREATE_SYSTEM,
        context_value=_fresh_request(authenticated_context),
        variable_values={"input": {"name": "Atlas", "axes": [{"name": "z", "type": "SPACE", "unit": "micrometer"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]}},
    )
    assert not hub.errors, hub.errors
    hub_id = hub.data["createCoordinateSystem"]["id"]

    drawable = await seed.create_array_dataset(authenticated_context, "Drawable", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    drawable_system = await sync_to_async(lambda: drawable.intrinsic_coordinate_system)()
    await _register(authenticated_context, drawable_system.pk, hub_id, {"kind": "SCALE", "scale": [1.0, 1.0, 1.0]})

    # Registered here, and the registration declares that no point of it corresponds: the data
    # is a candidate the builder will look at and nothing places.
    lost = await seed.create_array_dataset(authenticated_context, "Lost", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    lost_system = await sync_to_async(lambda: lost.intrinsic_coordinate_system)()
    await _register(authenticated_context, lost_system.pk, hub_id, {"kind": "UNMAPPABLE", "reason": "the stage position was never recorded"})

    async def _build(skip: bool):
        return await schema.execute(
            SCENE_FROM_SYSTEM,
            context_value=_fresh_request(authenticated_context),
            variable_values={"input": {"coordinateSystem": hub_id, "policy": {"skipUnplaceable": skip}}},
        )

    refused = await _build(False)
    assert refused.errors, "the default is the same line createLayer holds"

    built = await _build(True)
    assert not built.errors, built.errors
    assert built.data["createSceneFromCoordinateSystem"]["layers"], "the drawable source still becomes a layer"

    layers = await _layers(authenticated_context, built.data["createSceneFromCoordinateSystem"]["id"])
    assert all(layer["asAffine"] is not None for layer in layers), "every layer the builder made condenses"
