"""Scenes composing over a shared space: adoption, one truth per space, and lifecycles.

A scene's ``world`` says WHICH space it composes over; it stops being ownership the
moment the space is an adopted space. Under RFC-6 there is no membership set: a
registration into a shared space is unique per data-tree and places in *every* scene
over that space -- a rival alignment is a claim into a different space, never a second
row in this one. These tests pin the consequences: the collision guard refusing the
rival, two truths living in two spaces, deletion of the claim genuinely un-placing, and
the space outliving every scene over it.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.models import RestrictedError
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) {
    id name
    worldCoordinateSystem { id  epoch residents { __typename } }
  }
}
"""

SCENE_LAYERS = """
query SceneLayers($id: ID!) {
  scene(id: $id) {
    layers {
      id
      placement
      pathToWorld { transformation { id } }
    }
  }
}
"""

DELETE_SCENE = """
mutation Delete($input: DeleteSceneInput!) {
  deleteScene(input: $input)
}
"""

#: A 2-axis identity and a y-shifted rival, rows N_out x (N_in + 1).
IDENTITY_2D = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
SHIFTED_2D = [[1.0, 0.0, 500.0], [0.0, 1.0, 0.0]]


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity, so the scene-graph memo cannot go stale."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


def _make_space(ctx: HttpContext, name: str = "space", axis_type: str = enums.AxisTypeChoices.SPACE.value) -> "models.CoordinateSystem":
    """An ownerless shared space with calibrated y/x axes (or a deliberately non-navigable pair)."""
    space = models.CoordinateSystem.objects.create(name=name, organization=ctx.request.organization)
    for index, axis_name in enumerate(["y", "x"]):
        models.Axis.objects.create(coordinate_system=space, order=index, name=axis_name, type=axis_type, unit="micrometer" if axis_type == enums.AxisTypeChoices.SPACE.value else "a.u.")
    return space


async def _adopt(ctx: HttpContext, space: "models.CoordinateSystem", name: str) -> dict:
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": name, "coordinateSystem": str(space.pk)}})
    assert not result.errors, result.errors
    return result.data["createScene"]


def _register_into(ctx: HttpContext, source: "models.CoordinateSystem", space: "models.CoordinateSystem", affine: list) -> "models.Transformation":
    """One authored registration source -> space: the space's one truth for this data.

    Through the real writer, so the one-claim-per-space guard runs exactly as it
    would for a client.
    """
    return graph_logic.build_registration_edge(
        input_system=source,
        output_system=space,
        kind=enums.TransformKind.AFFINE,
        affine=affine,
        ctx=seed._creation(ctx),
    )


def _image_layer(scene_pk: str, lens: "models.Lens") -> "models.Layer":
    return models.Layer.objects.create(kind=enums.LayerKindChoices.IMAGE.value, scene_id=scene_pk, lens=lens)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rival_registration_into_one_shared_world_is_accepted(authenticated_context: HttpContext):
    """Rivals are allowed (RFC-9), and every scene over the space sees the same winner.

    RFC-6 refused the second claim of one dataset into one space, on the grounds that where
    data sits must have exactly one current answer. RFC-9 reverses that: the answer is now
    settled by a stated tie-break rather than by the data refusing to hold two, and the
    loser stays visible instead of never being writable.

    What survives unchanged is the property that mattered downstream -- every scene over one
    space composes the *same* route, because the choice is a function of the edges and not
    of the scene. There is still no membership to disagree through.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Shared", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    space = await sync_to_async(_make_space)(authenticated_context)
    scene_a = await _adopt(authenticated_context, space, "A")
    scene_b = await _adopt(authenticated_context, space, "B")
    assert scene_a["worldCoordinateSystem"]["id"] == scene_b["worldCoordinateSystem"]["id"] == str(space.pk)

    def setup():
        intrinsic = dataset.intrinsic_coordinate_system
        edge = _register_into(authenticated_context, intrinsic, space, IDENTITY_2D)
        _image_layer(scene_a["id"], lens)
        _image_layer(scene_b["id"], lens)
        return intrinsic, edge

    intrinsic, edge = await sync_to_async(setup)()

    rival = await sync_to_async(_register_into)(authenticated_context, intrinsic, space, SHIFTED_2D)
    assert rival.pk != edge.pk, "the second claim is written rather than refused"

    chosen = set()
    for scene in (scene_a, scene_b):
        result = await schema.execute(SCENE_LAYERS, context_value=_fresh_request(authenticated_context), variable_values={"id": scene["id"]})
        assert not result.errors, result.errors
        (layer,) = result.data["scene"]["layers"]
        assert layer["placement"] == "PLACED"
        chosen.add(layer["pathToWorld"][-1]["transformation"]["id"])

    assert len(chosen) == 1, f"every scene over one space must compose the same route, not one each: {chosen}"
    assert chosen <= {str(edge.pk), str(rival.pk)}, "and it must be one of the two claims actually authored"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_truths_live_in_two_spaces(authenticated_context: HttpContext):
    """The rival that the one space refused is at home in a fork of the space.

    registration1 and registration2 are claims into different worlds: two spaces, two
    scenes, and each scene's layer ends in its own space's truth.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Forked", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    space_v1 = await sync_to_async(_make_space)(authenticated_context, name="space-v1")
    space_v2 = await sync_to_async(_make_space)(authenticated_context, name="space-v2")
    scene_a = await _adopt(authenticated_context, space_v1, "A")
    scene_b = await _adopt(authenticated_context, space_v2, "B")

    def setup():
        intrinsic = dataset.intrinsic_coordinate_system
        edge_a = _register_into(authenticated_context, intrinsic, space_v1, IDENTITY_2D)
        edge_b = _register_into(authenticated_context, intrinsic, space_v2, SHIFTED_2D)
        _image_layer(scene_a["id"], lens)
        _image_layer(scene_b["id"], lens)
        return edge_a, edge_b

    edge_a, edge_b = await sync_to_async(setup)()

    for scene, expected in ((scene_a, edge_a), (scene_b, edge_b)):
        result = await schema.execute(SCENE_LAYERS, context_value=_fresh_request(authenticated_context), variable_values={"id": scene["id"]})
        assert not result.errors, result.errors
        (layer,) = result.data["scene"]["layers"]
        assert layer["placement"] == "PLACED"
        assert layer["pathToWorld"][-1]["transformation"]["id"] == str(expected.pk), "each scene's layer must end in its own space's claim, never the other's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_registration_unplaces_the_layer(authenticated_context: HttpContext):
    """The claim is what places: deleting it un-places the layer in every scene over the space.

    There is no membership to withdraw -- un-registering IS deleting the edge, and the
    layer degrades to UNREGISTERED rather than being deleted with it."""
    dataset = await seed.create_array_dataset(authenticated_context, "Removable", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    space = await sync_to_async(_make_space)(authenticated_context)
    scene = await _adopt(authenticated_context, space, "Removal")

    def setup():
        edge = _register_into(authenticated_context, dataset.intrinsic_coordinate_system, space, IDENTITY_2D)
        _image_layer(scene["id"], lens)
        return edge

    edge = await sync_to_async(setup)()

    placed = await schema.execute(SCENE_LAYERS, context_value=_fresh_request(authenticated_context), variable_values={"id": scene["id"]})
    assert placed.data["scene"]["layers"][0]["placement"] == "PLACED"

    await sync_to_async(edge.delete)()

    after = await schema.execute(SCENE_LAYERS, context_value=_fresh_request(authenticated_context), variable_values={"id": scene["id"]})
    (layer,) = after.data["scene"]["layers"]
    assert layer["pathToWorld"] is None, "a deleted claim must not place its layer"
    assert layer["placement"] == "UNREGISTERED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_scene_adopts_any_space(authenticated_context: HttpContext):
    """Adoption composes over the space as it is: axes and epoch come from the system.

    **Every** space is adoptable under RFC-9 -- a scene never owns its world, and a lens'
    crop is a space like any other, related to the grid it slices by an edge. The last
    refusal on kind grounds is gone with `isAdoptableWorld`; what remains is the one
    substantive check, that a space has an axis a camera can move along."""
    space = await sync_to_async(_make_space)(authenticated_context)

    scene = await _adopt(authenticated_context, space, "Adopted")
    assert scene["worldCoordinateSystem"]["id"] == str(space.pk)
    assert scene["worldCoordinateSystem"]["residents"] == [], "an ordinary reference frame: nothing lives in it"

    # axes or epoch alongside coordinateSystem: the space already has both.
    for extra in ({"axes": [{"name": "y", "type": "SPACE", "unit": "micrometer"}]}, {"epoch": "2026-07-15T12:00:00Z"}):
        result = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "nope", "coordinateSystem": str(space.pk), **extra}})
        assert result.errors and "takes its axes and epoch from it" in str(result.errors[0])

    # Another scene's world is nobody's property: a second scene composes right over it.
    first = await seed.create_scene(authenticated_context, "First")
    first_world = await sync_to_async(lambda: first.world)()
    second = await _adopt(authenticated_context, first_world, "Second")
    assert second["worldCoordinateSystem"]["id"] == str(first_world.pk), "two scenes, one space"

    # A lens' cropped grid used to be refused as "a slice of a space, not a space". Under
    # residence it is a space a lens lives in, and composing there is unusual, not wrong.
    dataset = await seed.create_array_dataset(authenticated_context, "Cropped", axes=seed.YX_AXES, shapes=[[64, 64]])
    sliced = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    lens_system = await sync_to_async(lambda: sliced.coordinate_system)()
    accepted = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "over the crop", "coordinateSystem": str(lens_system.pk)}})
    assert not accepted.errors, accepted.errors

    # A space with no navigable axis has nowhere to put anything.
    flat = await sync_to_async(_make_space)(authenticated_context, name="channels-only", axis_type=enums.AxisTypeChoices.CHANNEL.value)
    rejected = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "nope", "coordinateSystem": str(flat.pk)}})
    assert rejected.errors and "navigable" in str(rejected.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_scene_leaves_the_space_and_its_sibling_standing(authenticated_context: HttpContext):
    """A space outlives every scene over it -- deleting a scene never deletes a world."""
    dataset = await seed.create_array_dataset(authenticated_context, "Survivor", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    space = await sync_to_async(_make_space)(authenticated_context)
    scene_a = await _adopt(authenticated_context, space, "Doomed")
    scene_b = await _adopt(authenticated_context, space, "Survivor")

    def setup():
        edge = _register_into(authenticated_context, dataset.intrinsic_coordinate_system, space, IDENTITY_2D)
        _image_layer(scene_a["id"], lens)
        _image_layer(scene_b["id"], lens)
        return edge

    edge = await sync_to_async(setup)()

    deleted = await schema.execute(DELETE_SCENE, context_value=authenticated_context, variable_values={"input": {"id": scene_a["id"]}})
    assert not deleted.errors, deleted.errors

    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=space.pk).exists)(), "an adopted space is referenced, never owned"
    assert await sync_to_async(models.Transformation.objects.filter(pk=edge.pk).exists)(), "the registration is a fact about the space, not about the dead scene"
    survivor = await schema.execute(SCENE_LAYERS, context_value=_fresh_request(authenticated_context), variable_values={"id": scene_b["id"]})
    assert survivor.data["scene"]["layers"][0]["placement"] == "PLACED", "the sibling scene's composition is untouched"

    # A world created alongside a bare scene is no different: the scene never owned it,
    # so its deletion leaves the space standing -- removing a space is deleteCoordinateSystem's
    # explicit job, and an empty leftover world qualifies.
    bare = await seed.create_scene(authenticated_context, "Bare")
    bare_world_pk = await sync_to_async(lambda: bare.world_id)()
    deleted = await schema.execute(DELETE_SCENE, context_value=authenticated_context, variable_values={"input": {"id": str(bare.pk)}})
    assert not deleted.errors, deleted.errors
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=bare_world_pk).exists)(), "no scene deletion ever deletes a space"

    removed = await schema.execute(
        "mutation ($input: DeleteCoordinateSystemInput!) { deleteCoordinateSystem(input: $input) }",
        context_value=authenticated_context,
        variable_values={"input": {"id": str(bare_world_pk)}},
    )
    assert not removed.errors, removed.errors
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(pk=bare_world_pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shared_space_cannot_be_deleted_out_from_under_a_scene(authenticated_context: HttpContext):
    """Scene.world is RESTRICT: a space is never deleted while a scene composes over it --
    but the restriction yields when the whole organization goes (the RESTRICT-vs-PROTECT trap)."""
    space = await sync_to_async(_make_space)(authenticated_context)
    await _adopt(authenticated_context, space, "Holder")

    with pytest.raises(RestrictedError):
        await sync_to_async(space.delete)()

    # An org cascade collects the scenes too, so the restriction clears against the
    # deletion set instead of raising -- exactly what PROTECT would get wrong.
    organization = authenticated_context.request.organization
    await sync_to_async(organization.delete)()
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(pk=space.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_builder_over_a_space_of_unrenderable_sources_makes_no_layers(authenticated_context: HttpContext):
    """A source too small to render becomes no layer -- and its claim is untouched.

    The registration is a fact about the *space*, not about the scene the builder is
    making: skipping the layer does not unmake it, and the space still holds the claim."""
    tiny = await seed.create_array_dataset(authenticated_context, "Tiny", axes=seed.YX_AXES, shapes=[[64, 1]])
    space = await sync_to_async(_make_space)(authenticated_context)

    def register():
        return graph_logic.build_registration_edge(
            input_system=tiny.intrinsic_coordinate_system,
            output_system=space,
            kind=enums.TransformKind.BY_DIMENSION,
            name=None,
            scale=None,
            translation=None,
            affine=None,
            input_axes=["y", "x"],
            output_axes=["y", "x"],
            field=None,
            reason=None,
            validity=enums.PlacementValidity.VALIDATED,
            ctx=seed._creation(authenticated_context),
        )

    await sync_to_async(register)()

    result = await schema.execute(
        """
        mutation FromCS($input: CreateSceneFromCoordinateSystemInput!) {
          createSceneFromCoordinateSystem(input: $input) {
            id
            layers { id }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"coordinateSystem": str(space.pk), "policy": {}}},
    )
    assert not result.errors, result.errors
    scene = result.data["createSceneFromCoordinateSystem"]
    assert scene["layers"] == []

    def claims_into_space() -> int:
        return models.Transformation.objects.filter(parent__isnull=True, output=space).count()

    assert await sync_to_async(claims_into_space)() == 1, "the claim is the space's fact; a skipped layer does not unmake it"
