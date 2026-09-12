"""The coordinate space's own API surface: what a space says about itself, and its lifecycle.

``residents`` is what a space says about itself (RFC-9): the data living in it, and the
successor to the old four-value ``kind``. A space with **no** residents is a pure reference
frame -- a world, an atlas -- and it is exactly those that have a lifecycle here, because
they answer to nobody: scenes adopt one but never own it, so no scene's deletion removes a
space, and without ``deleteCoordinateSystem`` a mistyped atlas would outlive every
correction anyone could make to it. A space data lives in is described by that data, so
renaming or deleting it is refused: it is not an edit of the space but of the data.

Every delete here runs as ``bot_context``, not the admin fixture: ``assert_can_delete`` waves
org admins through before it ever reads the ownership callable, so an admin-only test cannot
tell a working guard from one that raises AttributeError on a column the model does not have.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed
# The collection fixtures live with the layer-kind tests, which is where a mesh or table
# collection is otherwise built; the owner union is the only other thing that needs one.
from tests.test_layer_kinds import _seed_mesh_collection_sync, _seed_table_dataset_sync


CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id name residents { __typename } }
}
"""

UPDATE_CS = """
mutation UpdateCS($input: UpdateCoordinateSystemInput!) {
  updateCoordinateSystem(input: $input) { id name epoch }
}
"""

DELETE_CS = """
mutation DeleteCS($input: DeleteCoordinateSystemInput!) {
  deleteCoordinateSystem(input: $input)
}
"""

SYSTEM = """
query System($id: ID!) {
  coordinateSystem(id: $id) {
    id
    creator { id }
    residents {
      __typename
      ... on ArrayDataset { id name }
      ... on Lens { id }
      ... on DataArray { id level }
      ... on MeshCollection { id version }
      ... on TableDataset { id name }
    }
  }
}
"""

LIST_SYSTEMS = """
query Systems($uninhabited: Boolean!) {
  coordinateSystems(filters: { uninhabited: $uninhabited }) { id residents { __typename } }
}
"""

ATLAS_AXES = [
    {"name": "y", "type": "SPACE", "unit": "micrometer"},
    {"name": "x", "type": "SPACE", "unit": "micrometer"},
]


async def _create_space(ctx: HttpContext, name: str = "Atlas", axes=None, registrations=None) -> dict:
    result = await schema.execute(
        CREATE_CS,
        context_value=ctx,
        variable_values={"input": {"name": name, "axes": axes if axes is not None else ATLAS_AXES, "registrations": registrations or []}},
    )
    assert not result.errors, result.errors
    return result.data["createCoordinateSystem"]


async def _system(ctx: HttpContext, system_id: str) -> dict:
    result = await schema.execute(SYSTEM, context_value=ctx, variable_values={"id": str(system_id)})
    assert not result.errors, result.errors
    return result.data["coordinateSystem"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scenes_world_is_an_ordinary_shared_space(authenticated_context: HttpContext):
    """A world created alongside a scene is the same thing as an atlas: a space nothing lives in.

    Scenes never own a space, so the space a bare `createScene` makes has no residents,
    other scenes may adopt it, and it outlives them all -- exactly like a space created
    directly with `createCoordinateSystem`. "Uninhabited" is the whole of what the old
    SHARED label meant.
    """
    atlas = await _create_space(authenticated_context, "Atlas")
    assert atlas["residents"] == [], "a space created to be registered into holds no data of its own"

    scene = await seed.create_scene(authenticated_context, "Bare")
    world = await sync_to_async(lambda: scene.world)()
    shared = await _system(authenticated_context, world.pk)

    assert shared["residents"] == [], "a scene adopts its world; nothing of the scene lives in it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_lives_in_its_own_space_and_a_scene_may_root_there(authenticated_context: HttpContext):
    """Every space is adoptable now, including a lens' crop -- the RFC-9 reversal.

    `isAdoptableWorld` refused an ARRAY system on the grounds that a slice *of* a space is
    not a space to compose in. Under residence a lens' crop is a space like any other,
    related to the dataset's grid by an edge, so there is nothing left for the refusal to
    stand on: composing there is unusual rather than wrong.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()

    owned = await _system(authenticated_context, intrinsic.pk)
    kinds = {resident["__typename"] for resident in owned["residents"]}
    assert "ArrayDataset" in kinds
    assert "DataArray" in kinds, "level 0 lives in the dataset's grid rather than owning a duplicate of it"

    lens = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    lens_system = await sync_to_async(lambda: lens.coordinate_system)()
    assert lens_system.pk != intrinsic.pk, "a sliced lens gets a space of its own"

    sliced = await _system(authenticated_context, lens_system.pk)
    assert [resident["__typename"] for resident in sliced["residents"]] == ["Lens"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_residents_name_the_data_living_in_a_space(authenticated_context: HttpContext):
    """`residents` resolves to the data itself, and a calibrated space has none.

    The sharpest case is the calibration. It used to hang off the dataset by a second FK,
    and `kind` was what told that relationship apart from the intrinsic one. Under RFC-9 it
    is simply a space with an edge into it -- **nothing lives there** -- which is why it
    reads exactly like an atlas, and why the edge is the only thing relating it to anything.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Owned", axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()

    residents = (await _system(authenticated_context, intrinsic.pk))["residents"]
    named = {resident["__typename"]: resident for resident in residents}
    assert named["ArrayDataset"]["name"] == "Owned"

    calibration = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        scale=[0.325, 0.325],
    )
    physical = await _system(authenticated_context, calibration.pk)
    assert physical["residents"] == [], "a calibrated space is a space with an edge into it, not a thing a dataset owns"

    scene = await seed.create_scene(authenticated_context, "Bare")
    world = await sync_to_async(lambda: scene.world)()
    assert (await _system(authenticated_context, world.pk))["residents"] == []

    atlas = await _create_space(authenticated_context, "Atlas")
    assert (await _system(authenticated_context, atlas["id"]))["residents"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_collection_lives_in_its_own_space(authenticated_context: HttpContext):
    """The two arms a dataset-shaped test never reaches.

    All three used to read INTRINSIC, so `kind` could not tell them apart and `owner` was
    the field that could. `residents` answers both questions at once: what is here, and
    what sort of thing it is.
    """
    collection = await sync_to_async(_seed_mesh_collection_sync)(authenticated_context)
    mesh_system = await sync_to_async(lambda: collection.coordinate_system)()
    mesh = (await _system(authenticated_context, mesh_system.pk))["residents"]
    assert [resident["__typename"] for resident in mesh] == ["MeshCollection"]
    assert mesh[0]["version"] == "v1"

    table = await sync_to_async(_seed_table_dataset_sync)(authenticated_context, "localizations")
    table_system = await sync_to_async(lambda: table.coordinate_system)()
    resolved = (await _system(authenticated_context, table_system.pk))["residents"]
    assert [resident["__typename"] for resident in resolved] == ["TableDataset"]
    assert resolved[0]["name"] == "localizations"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_uninhabited_lists_every_registrable_space(authenticated_context: HttpContext):
    """`uninhabited: true` is THE predicate for registrable spaces -- a scene's world included.

    There is no second sort of reference frame: the world a bare scene gets and an atlas
    created directly are the same thing, so one filter finds them both and the spaces data
    lives in stay out.
    """
    atlas = await _create_space(authenticated_context, "Atlas")
    scene = await seed.create_scene(authenticated_context, "Bare")
    world = await sync_to_async(lambda: scene.world)()
    dataset = await seed.create_array_dataset(authenticated_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])

    result = await schema.execute(LIST_SYSTEMS, context_value=authenticated_context, variable_values={"uninhabited": True})
    assert not result.errors, result.errors
    empty = {system["id"] for system in result.data["coordinateSystems"]}
    assert empty == {atlas["id"], str(world.pk)}, "every space nothing lives in, scene worlds included"

    result = await schema.execute(LIST_SYSTEMS, context_value=authenticated_context, variable_values={"uninhabited": False})
    assert not result.errors, result.errors
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()
    inhabited = {system["id"] for system in result.data["coordinateSystems"]}
    assert str(intrinsic.pk) in inhabited and atlas["id"] not in inhabited


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shared_space_can_be_renamed_and_its_clock_anchored(authenticated_context: HttpContext):
    """The fields that describe the space itself. Where its data sits stays an edge."""
    space = await _create_space(authenticated_context, "Atals")  # the typo this mutation exists for

    result = await schema.execute(
        UPDATE_CS,
        context_value=authenticated_context,
        variable_values={"input": {"id": space["id"], "name": "Atlas", "epoch": "2026-07-16T00:00:00+00:00"}},
    )
    assert not result.errors, result.errors
    assert result.data["updateCoordinateSystem"]["name"] == "Atlas"
    assert result.data["updateCoordinateSystem"]["epoch"] is not None

    # Partial: a rename does not clear the clock someone just anchored.
    result = await schema.execute(UPDATE_CS, context_value=authenticated_context, variable_values={"input": {"id": space["id"], "name": "Atlas v2"}})
    assert not result.errors, result.errors
    assert result.data["updateCoordinateSystem"]["epoch"] is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_data_lives_in_cannot_be_renamed_or_deleted_directly(authenticated_context: HttpContext):
    """Both mutations refuse a space with residents: it is described by the data in it.

    The guard used to read "is this SHARED", which was the same question asked through the
    owner FKs. It now asks the thing directly -- does anything live here -- and the error
    names the resident, which the old one could not.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()

    renamed = await schema.execute(UPDATE_CS, context_value=authenticated_context, variable_values={"input": {"id": str(intrinsic.pk), "name": "nope"}})
    assert renamed.errors and "data lives in it" in str(renamed.errors[0])
    assert "ArrayDataset" in str(renamed.errors[0]), "the refusal names what is in the way"

    deleted = await schema.execute(DELETE_CS, context_value=authenticated_context, variable_values={"input": {"id": str(intrinsic.pk)}})
    assert deleted.errors and "data lives in it" in str(deleted.errors[0])
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=intrinsic.pk).exists)(), "the dataset's spatial graph survives"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unused_space_is_deletable_by_its_creator(bot_context: HttpContext):
    """The whole point: a space created by mistake can be taken back.

    Runs as a non-admin, so `assert_can_delete` actually consults the ownership callable
    rather than short-circuiting -- which is what would have caught `self_owner` reading a
    `created_through_by` column the coordinate graph does not have.
    """
    space = await _create_space(bot_context, "Mistake")

    result = await schema.execute(DELETE_CS, context_value=bot_context, variable_values={"input": {"id": space["id"]}})
    assert not result.errors, result.errors
    assert result.data["deleteCoordinateSystem"] == space["id"]
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(pk=space["id"]).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_someone_else_created_is_not_yours_to_delete(authenticated_context: HttpContext, bot_context: HttpContext):
    """The ownership guard, reached only because the caller is not an org admin."""
    space = await _create_space(authenticated_context, "Theirs")

    result = await schema.execute(DELETE_CS, context_value=bot_context, variable_values={"input": {"id": space["id"]}})
    assert result.errors and "Only the creator or assignee can delete this" in str(result.errors[0])
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=space["id"]).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_in_use_is_refused_rather_than_cascaded_away(bot_context: HttpContext):
    """Each refusal guards a CASCADE that would take something the caller never named."""
    dataset = await seed.create_array_dataset(bot_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])
    registration = {"dataset": str(dataset.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}
    space = await _create_space(bot_context, "Populated", registrations=[registration])

    # An edge registered into it: deleting the space would delete a placement someone authored.
    result = await schema.execute(DELETE_CS, context_value=bot_context, variable_values={"input": {"id": space["id"]}})
    assert result.errors and "transformation edge" in str(result.errors[0])

    # Remove the registration, and the same space goes.
    edge = await sync_to_async(models.Transformation.objects.get)(output__pk=space["id"], parent__isnull=True)
    await sync_to_async(edge.delete)()
    result = await schema.execute(DELETE_CS, context_value=bot_context, variable_values={"input": {"id": space["id"]}})
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_a_scene_composes_over_is_refused(bot_context: HttpContext):
    """Scene.world is RESTRICT; the mutation names the scenes instead of raising an IntegrityError."""
    space = await _create_space(bot_context, "Adopted")
    created = await schema.execute(
        "mutation ($input: CreateSceneFromCoordinateSystemInput!) { createSceneFromCoordinateSystem(input: $input) { id } }",
        context_value=bot_context,
        variable_values={"input": {"coordinateSystem": space["id"]}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(DELETE_CS, context_value=bot_context, variable_values={"input": {"id": space["id"]}})
    assert result.errors and "is the world of" in str(result.errors[0])
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=space["id"]).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refining_an_edge_leaves_a_readable_audit_trail(authenticated_context: HttpContext):
    """RFC-6 Rule 1 is only true if a client can read it.

    "updateTransformation refines an edge in place; koherent provenance holds the history"
    -- a refinement rewrites the row, so the audit trail is the *only* place the placement's
    earlier states exist. The field is on the Transformation interface, so a concrete kind
    (here SCALE) inherits it.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()
    space = await _create_space(authenticated_context, "Atlas")

    created = await schema.execute(
        "mutation ($input: CreateTransformationInput!) { createTransformation(input: $input) { id version } }",
        context_value=authenticated_context,
        variable_values={"input": {"input": str(intrinsic.pk), "output": space["id"], "transform": {"kind": "SCALE", "scale": [0.5, 0.5]}}},
    )
    assert not created.errors, created.errors
    edge_id = created.data["createTransformation"]["id"]

    refined = await schema.execute(
        "mutation ($input: UpdateTransformationInput!) { updateTransformation(input: $input) { id version } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": edge_id, "scale": [0.51, 0.51]}},
    )
    assert not refined.errors, refined.errors
    assert refined.data["updateTransformation"]["version"] == 2

    result = await schema.execute(
        """
        query ($id: ID!) {
          transformation(id: $id) {
            id version creator { id }
            ... on ScaleTransformation { scale }
            provenanceEntries { kind }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": edge_id},
    )
    assert not result.errors, result.errors
    edge = result.data["transformation"]

    assert edge["scale"] == [0.51, 0.51], "the row itself carries only the current truth"
    # Two states of one placement, sequentially, in the audit trail: the create and the refine.
    kinds = [entry["kind"] for entry in edge["provenanceEntries"]]
    assert len(kinds) == 2, f"expected a CREATE and an UPDATE entry, got {kinds}"
    assert edge["creator"] is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shared_space_takes_its_axes_as_given_but_must_have_some(authenticated_context: HttpContext):
    """No type ordering is asked of a shared space; a space with no axes is still not a space.

    Space-before-time was refused here. It bought nothing: the render-axis derivation finds the
    time axis by type, so `x, t` and `t, x` derive the same answer, and a world declared in the
    order a caller's data has it was turned away for a rule nothing reads. Having *no* axes is
    the different case -- such a space composes into nothing and every edge into it fails the
    rank check, so it is refused at the door and rolled back.
    """
    unordered = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "Space before time",
                "axes": [
                    {"name": "x", "type": "SPACE", "unit": "micrometer"},
                    {"name": "t", "type": "TIME", "unit": "second"},
                ],
                "registrations": [],
            }
        },
    )
    assert not unordered.errors, str(unordered.errors and unordered.errors[0])

    empty = await schema.execute(
        CREATE_CS,
        context_value=authenticated_context,
        variable_values={"input": {"name": "Empty", "axes": [], "registrations": []}},
    )
    assert empty.errors and "no axes" in str(empty.errors[0])
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(name="Empty").exists)(), "the rollback leaves no permanent zero-axis space"
