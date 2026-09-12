"""A tour is authored as a whole, and a scene says how it wants to be looked at.

Two rules carry this. `order` is written by enumeration over the authored list and never
supplied by a caller -- which is what makes "the third stop" well defined, and what keeps
the (animation, order) uniqueness safe without deferral, since a stop is never swapped in
place. And a camera position is keyed by the world's axis names, so a name the world does
not have is a typo the server can catch at authoring time rather than a pose that silently
does nothing when the tour is played.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateAnimationInput!) {
  createAnimation(input: $input) {
    id
    name
    waypoints { order name durationMs easing camera { position crossSectionScale projectionOrientation } }
  }
}
"""

UPDATE = """
mutation Update($input: UpdateAnimationInput!) {
  updateAnimation(input: $input) {
    id
    name
    description
    waypoints { order name }
  }
}
"""

DELETE = """
mutation Delete($input: DeleteAnimationInput!) {
  deleteAnimation(input: $input)
}
"""

LIST = """
query List($filters: AnimationFilter) {
  animations(filters: $filters) { id name }
}
"""

SCENE = """
query Scene($id: ID!) {
  scene(id: $id) { preferredView backgroundColor animations { name } }
}
"""

UPDATE_SCENE = """
mutation UpdateScene($input: UpdateSceneInput!) {
  updateScene(input: $input) { id preferredView backgroundColor }
}
"""

CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) { id preferredView backgroundColor }
}
"""


def _waypoint(z: float, name: str | None = None, **kwargs) -> dict:
    """A pose on the seeded world, whose axes are z, y, x."""
    payload = {"camera": {"position": {"z": z, "y": 32.0, "x": 32.0}, "crossSectionScale": 1.5}}
    if name is not None:
        payload["name"] = name
    payload.update(kwargs)
    return payload


async def _create(ctx: HttpContext, scene, waypoints, name="Tour"):
    result = await schema.execute(CREATE, context_value=ctx, variable_values={"input": {"scene": str(scene.pk), "name": name, "waypoints": waypoints}})
    return result


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_order_is_written_by_enumeration(db, authenticated_context: HttpContext):
    """The client never sends an index; the list order is the tour order."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    result = await _create(ctx, scene, [_waypoint(0, "Start"), _waypoint(10, "Middle"), _waypoint(20, "End")])
    assert not result.errors, result.errors

    waypoints = result.data["createAnimation"]["waypoints"]
    assert [w["order"] for w in waypoints] == [0, 1, 2]
    assert [w["name"] for w in waypoints] == ["Start", "Middle", "End"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_camera_round_trips(db, authenticated_context: HttpContext):
    """What goes in comes out -- including a null orientation, which is 'viewer decides'."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    result = await _create(
        ctx,
        scene,
        [{"camera": {"position": {"z": 4.0, "y": 1.0, "x": 2.0}, "crossSectionScale": 0.25, "projectionOrientation": [0.0, 0.0, 0.0, 1.0]}}],
    )
    assert not result.errors, result.errors

    camera = result.data["createAnimation"]["waypoints"][0]["camera"]
    assert camera["position"] == {"z": 4.0, "y": 1.0, "x": 2.0}
    assert camera["crossSectionScale"] == 0.25
    assert camera["projectionOrientation"] == [0.0, 0.0, 0.0, 1.0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_timing_defaults_and_overrides(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    result = await _create(ctx, scene, [_waypoint(0), _waypoint(5, durationMs=250, easing="LINEAR")])
    assert not result.errors, result.errors

    waypoints = result.data["createAnimation"]["waypoints"]
    assert (waypoints[0]["durationMs"], waypoints[0]["easing"]) == (1000, "EASE_IN_OUT")
    assert (waypoints[1]["durationMs"], waypoints[1]["easing"]) == (250, "LINEAR")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_position_on_an_axis_the_world_lacks_is_rejected(db, authenticated_context: HttpContext):
    """A keyed position makes a typo silent -- so it is caught where it is authored."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    result = await _create(ctx, scene, [{"camera": {"position": {"zz": 4.0}}}])
    assert result.errors, "an axis the world does not have is not a position"
    message = str(result.errors[0])
    assert "zz" in message, "the error must name the offending axis"
    assert "'x'" in message and "'z'" in message, "and the axes that do exist"
    assert not await models.Animation.objects.filter(scene=scene).aexists(), "nothing is written"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_three_component_orientation_is_rejected(db, authenticated_context: HttpContext):
    """The plausible mistake: an euler triple where a quaternion belongs."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    result = await _create(ctx, scene, [{"camera": {"position": {"z": 1.0}, "projectionOrientation": [0.0, 0.0, 0.0]}}])
    assert result.errors, "a three-component orientation is not a quaternion"
    assert "quaternion" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_replaces_the_stops_and_re_enumerates(db, authenticated_context: HttpContext):
    """Replacing, not merging: a tour is a sequence, and reordering is re-authoring it."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _create(ctx, scene, [_waypoint(0, "A"), _waypoint(10, "B"), _waypoint(20, "C")])
    assert not created.errors, created.errors
    animation_id = created.data["createAnimation"]["id"]

    # Reversed and shorter: the tail is dropped and order re-enumerates from zero.
    result = await schema.execute(
        UPDATE,
        context_value=ctx,
        variable_values={"input": {"id": animation_id, "waypoints": [_waypoint(20, "C"), _waypoint(0, "A")]}},
    )
    assert not result.errors, result.errors
    assert [(w["order"], w["name"]) for w in result.data["updateAnimation"]["waypoints"]] == [(0, "C"), (1, "A")]
    assert await models.AnimationWaypoint.objects.filter(animation_id=animation_id).acount() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_leaves_omitted_fields_alone(db, authenticated_context: HttpContext):
    """An omitted field means 'leave it', never 'clear it'."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "name": "Tour", "waypoints": [_waypoint(0, "A")]}},
    )
    assert not created.errors, created.errors
    animation_id = created.data["createAnimation"]["id"]

    # Rename only: the stops must survive untouched.
    result = await schema.execute(UPDATE, context_value=ctx, variable_values={"input": {"id": animation_id, "name": "Renamed"}})
    assert not result.errors, result.errors
    assert result.data["updateAnimation"]["name"] == "Renamed"
    assert [w["name"] for w in result.data["updateAnimation"]["waypoints"]] == ["A"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_scene_deletes_the_tour_and_its_stops(db, authenticated_context: HttpContext):
    """Both hops cascade: a tour of a composition means nothing once the composition is gone."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _create(ctx, scene, [_waypoint(0), _waypoint(10)])
    assert not created.errors, created.errors
    animation_id = created.data["createAnimation"]["id"]

    await sync_to_async(scene.delete)()
    assert not await models.Animation.objects.filter(pk=animation_id).aexists()
    assert not await models.AnimationWaypoint.objects.filter(animation_id=animation_id).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scene_viewer_preferences(db, authenticated_context: HttpContext):
    """AUTO by default, settable at creation, and changeable afterwards -- the last is the
    point: a preference you cannot toggle is not a preference."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    data = await schema.execute(SCENE, context_value=ctx, variable_values={"id": str(scene.pk)})
    assert not data.errors, data.errors
    assert data.data["scene"]["preferredView"] == "AUTO"
    assert data.data["scene"]["backgroundColor"] is None

    result = await schema.execute(
        UPDATE_SCENE,
        context_value=ctx,
        variable_values={"input": {"id": str(scene.pk), "preferredView": "THREE_D", "backgroundColor": [0.0, 0.0, 0.0, 1.0]}},
    )
    assert not result.errors, result.errors
    assert result.data["updateScene"]["preferredView"] == "THREE_D"
    assert result.data["updateScene"]["backgroundColor"] == [0.0, 0.0, 0.0, 1.0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_scene_sets_the_preference(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    result = await schema.execute(CREATE_SCENE, context_value=ctx, variable_values={"input": {"name": "Volumetric", "preferredView": "THREE_D"}})
    assert not result.errors, result.errors
    assert result.data["createScene"]["preferredView"] == "THREE_D"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_scene_leaves_omitted_preferences_alone(db, authenticated_context: HttpContext):
    """Setting the background must not silently reset the view preference to null."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    await schema.execute(UPDATE_SCENE, context_value=ctx, variable_values={"input": {"id": str(scene.pk), "preferredView": "THREE_D"}})

    result = await schema.execute(UPDATE_SCENE, context_value=ctx, variable_values={"input": {"id": str(scene.pk), "backgroundColor": [1.0, 1.0, 1.0, 1.0]}})
    assert not result.errors, result.errors
    assert result.data["updateScene"]["preferredView"] == "THREE_D", "an omitted field is left alone, not cleared"
    assert result.data["updateScene"]["backgroundColor"] == [1.0, 1.0, 1.0, 1.0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_scene_refuses_another_organizations_scene(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """get_for_org is the whole gate here -- Scene has no creator to guard on."""
    theirs = await seed.create_scene(other_org_context, "Theirs")
    result = await schema.execute(UPDATE_SCENE, context_value=authenticated_context, variable_values={"input": {"id": str(theirs.pk), "preferredView": "THREE_D"}})
    assert result.errors, "another organization's scene is not ours to configure"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_guard(db, authenticated_context: HttpContext, bot_context: HttpContext):
    """Run as the bot: `assert_can_delete` short-circuits for org admins, and
    `authenticated_context` is one, so a guard test written with it proves nothing."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    mine = await _create(bot_context, scene, [_waypoint(0)], name="Mine")
    assert not mine.errors, mine.errors
    result = await schema.execute(DELETE, context_value=bot_context, variable_values={"input": {"id": mine.data["createAnimation"]["id"]}})
    assert not result.errors, result.errors

    theirs = await _create(ctx, scene, [_waypoint(0)], name="Theirs")
    assert not theirs.errors, theirs.errors
    result = await schema.execute(DELETE, context_value=bot_context, variable_values={"input": {"id": theirs.data["createAnimation"]["id"]}})
    assert result.errors, "a non-admin must not delete another user's tour"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_list_is_scoped_to_the_organization(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """A bare list field returns every row in the table until get_queryset scopes it."""
    ctx = authenticated_context
    await _create(ctx, await seed.create_scene(ctx, "Ours"), [_waypoint(0)], name="Ours")
    await _create(other_org_context, await seed.create_scene(other_org_context, "Theirs"), [_waypoint(0)], name="Theirs")

    async def names(context):
        result = await schema.execute(LIST, context_value=context, variable_values={"filters": {}})
        assert not result.errors, result.errors
        return {row["name"] for row in result.data["animations"]}

    assert await names(ctx) == {"Ours"}
    assert await names(other_org_context) == {"Theirs"}
