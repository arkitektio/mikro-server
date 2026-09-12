"""The bulk/lookup helper mutations around scenes and shared spaces.

Four doors the never-GC lifecycle needs: `clearScene` (drop a scene's layers, touch no
fact), `clearCoordinateSystem` (drop every registration into a space at once),
`deleteRegistration` (un-register by naming source and space, not the edge id) and
`deleteOrphanedCoordinateSystems` (sweep the spaces nothing points at anymore). Each
test pins what the helper must NOT touch as much as what it deletes -- and the guard
tests run as ``bot_context``, because an org admin short-circuits ``assert_can_delete``
and would prove nothing.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed


CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) { id }
}
"""

SCENE_LAYERS = """
query SceneLayers($id: ID!) {
  scene(id: $id) { layers { id placement } }
}
"""

CLEAR_SCENE = """
mutation Clear($input: ClearSceneInput!) {
  clearScene(input: $input) { id layers { id } }
}
"""

CLEAR_CS = """
mutation Clear($input: ClearCoordinateSystemInput!) {
  clearCoordinateSystem(input: $input)
}
"""

DELETE_REGISTRATION = """
mutation Unregister($input: DeleteRegistrationInput!) {
  deleteRegistration(input: $input)
}
"""

SWEEP = "mutation { deleteOrphanedCoordinateSystems }"

IDENTITY_2D = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def _make_space(ctx: HttpContext, name: str = "space") -> "models.CoordinateSystem":
    """An ownerless shared space with calibrated y/x axes, created by the context's user."""
    space = models.CoordinateSystem.objects.create(name=name, organization=ctx.request.organization, creator=ctx.request.user)
    for index, axis_name in enumerate(["y", "x"]):
        models.Axis.objects.create(coordinate_system=space, order=index, name=axis_name, type=enums.AxisTypeChoices.SPACE.value, unit="micrometer")
    return space


def _register_into(ctx: HttpContext, source: "models.CoordinateSystem", space: "models.CoordinateSystem") -> "models.Transformation":
    return graph_logic.build_registration_edge(
        input_system=source,
        output_system=space,
        kind=enums.TransformKind.AFFINE,
        affine=IDENTITY_2D,
        ctx=seed._creation(ctx),
    )


async def _adopt(ctx: HttpContext, space: "models.CoordinateSystem", name: str) -> str:
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": name, "coordinateSystem": str(space.pk)}})
    assert not result.errors, result.errors
    return result.data["createScene"]["id"]


def _image_layer(scene_pk: str, lens: "models.Lens") -> "models.Layer":
    return models.Layer.objects.create(kind=enums.LayerKindChoices.IMAGE.value, scene_id=scene_pk, lens=lens)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clear_scene_drops_layers_and_touches_no_fact(authenticated_context: HttpContext):
    """clearScene is a view-state reset: layers go, the space, the claim and the sibling scene stay."""
    dataset = await seed.create_array_dataset(authenticated_context, "Cleared", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    space = await sync_to_async(_make_space)(authenticated_context)
    scene_a = await _adopt(authenticated_context, space, "A")
    scene_b = await _adopt(authenticated_context, space, "B")

    def setup():
        edge = _register_into(authenticated_context, dataset.intrinsic_coordinate_system, space)
        _image_layer(scene_a, lens)
        _image_layer(scene_a, lens)
        _image_layer(scene_b, lens)
        return edge

    edge = await sync_to_async(setup)()

    result = await schema.execute(CLEAR_SCENE, context_value=authenticated_context, variable_values={"input": {"id": scene_a}})
    assert not result.errors, result.errors
    assert result.data["clearScene"] == {"id": scene_a, "layers": []}

    assert await sync_to_async(models.Scene.objects.filter(pk=scene_a).exists)(), "clearing is not deleting"
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=space.pk).exists)()
    assert await sync_to_async(models.Transformation.objects.filter(pk=edge.pk).exists)(), "a layer is how a scene shows data, not where data sits"
    assert await sync_to_async(models.Layer.objects.filter(scene_id=scene_b).count)() == 1, "the sibling scene's composition is untouched"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clear_coordinate_system_empties_inward_edges_only(authenticated_context: HttpContext):
    """Everything INTO the space goes; the space, its scenes and its own outward claim stay."""
    a = await seed.create_array_dataset(authenticated_context, "A", axes=seed.YX_AXES, shapes=[[64, 64]])
    b = await seed.create_array_dataset(authenticated_context, "B", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, a, slices=[])
    space = await sync_to_async(_make_space)(authenticated_context)
    wider = await sync_to_async(_make_space)(authenticated_context, name="wider")
    scene = await _adopt(authenticated_context, space, "Over it")

    def setup():
        edge_a = _register_into(authenticated_context, a.intrinsic_coordinate_system, space)
        edge_b = _register_into(authenticated_context, b.intrinsic_coordinate_system, space)
        outward = _register_into(authenticated_context, space, wider)
        _image_layer(scene, lens)
        return edge_a, edge_b, outward

    edge_a, edge_b, outward = await sync_to_async(setup)()

    result = await schema.execute(CLEAR_CS, context_value=authenticated_context, variable_values={"input": {"id": str(space.pk)}})
    assert not result.errors, result.errors
    assert sorted(result.data["clearCoordinateSystem"]) == sorted([str(edge_a.pk), str(edge_b.pk)])

    assert not await sync_to_async(models.Transformation.objects.filter(pk__in=[edge_a.pk, edge_b.pk]).exists)()
    assert await sync_to_async(models.Transformation.objects.filter(pk=outward.pk).exists)(), "an edge OUT of the space is its own claim into a wider one, not a registration into it"
    assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=space.pk).exists)()

    layers = await schema.execute(SCENE_LAYERS, context_value=authenticated_context, variable_values={"id": scene})
    (layer,) = layers.data["scene"]["layers"]
    assert layer["placement"] == "UNREGISTERED", "the scene survives; its layer degrades exactly as a single deleteTransformation would leave it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clear_coordinate_system_refusals(authenticated_context: HttpContext, bot_context: HttpContext):
    """A space data lives in is not clearable, and clearing is the space-creator's act."""
    dataset = await seed.create_array_dataset(authenticated_context, "Owned", axes=seed.YX_AXES, shapes=[[64, 64]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()

    result = await schema.execute(CLEAR_CS, context_value=authenticated_context, variable_values={"input": {"id": str(intrinsic.pk)}})
    assert result.errors and "data lives in it" in str(result.errors[0]), "a space described by its residents is not a space to empty"

    theirs = await sync_to_async(_make_space)(authenticated_context, name="Theirs")
    result = await schema.execute(CLEAR_CS, context_value=bot_context, variable_values={"input": {"id": str(theirs.pk)}})
    assert result.errors and "Only the creator" in str(result.errors[0])
    mine = await sync_to_async(_make_space)(bot_context, name="Mine")
    result = await schema.execute(CLEAR_CS, context_value=bot_context, variable_values={"input": {"id": str(mine.pk)}})
    assert not result.errors, result.errors
    assert result.data["clearCoordinateSystem"] == [], "an already-empty space clears to an empty list, not an error"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_registration_by_source_and_space(authenticated_context: HttpContext):
    """The edge is found from (source, space) via its claim root, and deleting it un-places."""
    dataset = await seed.create_array_dataset(authenticated_context, "Named", axes=seed.YX_AXES, shapes=[[64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    space = await sync_to_async(_make_space)(authenticated_context)
    scene = await _adopt(authenticated_context, space, "Watcher")

    def setup():
        edge = _register_into(authenticated_context, dataset.intrinsic_coordinate_system, space)
        _image_layer(scene, lens)
        return edge

    edge = await sync_to_async(setup)()

    result = await schema.execute(
        DELETE_REGISTRATION,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.pk), "world": str(space.pk)}},
    )
    assert not result.errors, result.errors
    assert result.data["deleteRegistration"] == [str(edge.pk)], "every edge from the source into the space goes, and here there is one"
    assert not await sync_to_async(models.Transformation.objects.filter(pk=edge.pk).exists)()

    layers = await schema.execute(SCENE_LAYERS, context_value=authenticated_context, variable_values={"id": scene})
    assert layers.data["scene"]["layers"][0]["placement"] == "UNREGISTERED", "un-registering IS un-placing, in every scene over the space"

    # Nothing left to match: the same call now names a registration that does not exist.
    again = await schema.execute(
        DELETE_REGISTRATION,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.pk), "world": str(space.pk)}},
    )
    assert again.errors and "no edge relates this source" in str(again.errors[0])

    # Naming the dataset's own space finds nothing either -- not because such a space is a
    # forbidden target (RFC-9 has no privileged class of space) but because a dataset has no
    # edge from itself to itself.
    intrinsic = await sync_to_async(lambda: dataset.coordinate_system)()
    owned = await schema.execute(
        DELETE_REGISTRATION,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.pk), "world": str(intrinsic.pk)}},
    )
    assert owned.errors and "no edge relates this source" in str(owned.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_registration_guards_the_edges_author(authenticated_context: HttpContext, bot_context: HttpContext):
    """A registration someone else authored is not yours to un-register (admins excepted)."""
    dataset = await seed.create_array_dataset(authenticated_context, "Guarded", axes=seed.YX_AXES, shapes=[[64, 64]])
    space = await sync_to_async(_make_space)(authenticated_context)
    edge = await sync_to_async(_register_into)(authenticated_context, await sync_to_async(lambda: dataset.intrinsic_coordinate_system)(), space)

    result = await schema.execute(
        DELETE_REGISTRATION,
        context_value=bot_context,
        variable_values={"input": {"dataset": str(dataset.pk), "world": str(space.pk)}},
    )
    assert result.errors and "Only the creator" in str(result.errors[0])
    assert await sync_to_async(models.Transformation.objects.filter(pk=edge.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_orphan_sweep_takes_only_true_orphans_you_own(authenticated_context: HttpContext, bot_context: HttpContext, other_org_context: HttpContext):
    """The sweep deletes ownerless spaces nothing points at -- scoped to your own unless admin.

    Four spaces in the org: a true orphan of the caller's, a true orphan of someone
    else's, one a scene roots in, one an edge touches. A non-admin sweep takes exactly
    the first; an admin sweep then takes the foreign orphan too. Another org's orphan
    is never visible to either.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Toucher", axes=seed.YX_AXES, shapes=[[64, 64]])

    def setup():
        mine = _make_space(bot_context, name="mine-orphan")
        theirs = _make_space(authenticated_context, name="their-orphan")
        adopted = _make_space(bot_context, name="adopted")
        touched = _make_space(bot_context, name="touched")
        _register_into(authenticated_context, dataset.intrinsic_coordinate_system, touched)
        foreign = models.CoordinateSystem.objects.create(name="foreign-orphan", organization=other_org_context.request.organization, creator=other_org_context.request.user)
        return mine, theirs, adopted, touched, foreign

    mine, theirs, adopted, touched, foreign = await sync_to_async(setup)()
    await _adopt(bot_context, adopted, "Rooted")

    swept = await schema.execute(SWEEP, context_value=bot_context)
    assert not swept.errors, swept.errors
    assert swept.data["deleteOrphanedCoordinateSystems"] == [str(mine.pk)], "a non-admin sweeps only the orphans they created -- foreign ones are skipped, not refused"
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(pk=mine.pk).exists)()
    for survivor in (theirs, adopted, touched, foreign):
        assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=survivor.pk).exists)(), survivor.name

    admin_swept = await schema.execute(SWEEP, context_value=authenticated_context)
    assert not admin_swept.errors, admin_swept.errors
    assert str(theirs.pk) in admin_swept.data["deleteOrphanedCoordinateSystems"], "an org admin sweeps every orphan"
    assert str(foreign.pk) not in admin_swept.data["deleteOrphanedCoordinateSystems"], "another organization's orphan is out of reach"
    assert not await sync_to_async(models.CoordinateSystem.objects.filter(pk=theirs.pk).exists)()
    for survivor in (adopted, touched, foreign):
        assert await sync_to_async(models.CoordinateSystem.objects.filter(pk=survivor.pk).exists)(), survivor.name
