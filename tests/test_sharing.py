"""A guest of the organization, granted a collaboration and nothing else: what they read, and what they may draw.

Their token lists their collaborations by service instance (`collaborations`, instance "1" in the test settings),
with the roles they hold in each; the guest token is minted per test. A full member ("test")
puts objects in a collaboration; what each needs to be seen comes along, and what is part of it is in it.
"""

import pytest
from asgiref.sync import sync_to_async
from authentikate import settings as authentikate_settings
from django.db.backends.utils import CursorWrapper
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from arkitekt_service.collaborations import items as collaboration_items
from arkitekt_service.collaborations.models import Collaboration
from core import models
from mikro_server.schema import schema
from tests import seed

SCENE = "query ($id: ID!) { scene(id: $id) { id name } }"
SCENES = "query { scenes { id } }"
DRAW = """
mutation ($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id collection { id scene { id } } }
}
"""
RENDER = """
query ($id: ID!) {
  scene(id: $id) {
    id
    worldCoordinateSystem { id }
    layers {
      id
      ... on IntensityLayer {
        lens { id dataset { id name dataArrays { id } } }
        levelPaths { path { inverted } dataArray { id } }
      }
    }
  }
}
"""
SHALLOW = "query ($id: ID!) { scene(id: $id) { id } }"
MAKE_LAYER = "mutation ($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }"


def _context(token: str) -> HttpContext:
    return HttpContext(request=UniversalRequest(_extensions={}), response=TemporalResponse(), headers={"Authorization": f"Bearer {token}"}, type="http")


@pytest.fixture
def guest(db, authenticated_context, settings):
    """``guest({collaboration: roles})``: a context presenting a guest's token granted those collaborations on instance "1"."""

    def mint(collaborations: dict) -> HttpContext:
        granted = {str(collaboration.pk if isinstance(collaboration, Collaboration) else collaboration): roles for collaboration, roles in collaborations.items()}
        settings.AUTHENTIKATE = {
            **settings.AUTHENTIKATE,
            "static_tokens": {**settings.AUTHENTIKATE["static_tokens"], "guest": {"sub": "20", "roles": [], "collabs": {"1": granted}, "scope": "openid mikro_all"}},
        }
        authentikate_settings.cached_settings = None
        return _context("guest")

    yield mint
    authentikate_settings.cached_settings = None


async def _run(ctx: HttpContext, query: str, **variables):
    return await schema.execute(query, context_value=ctx, variable_values=variables or None)


def _point(**target) -> dict:
    return {"input": {**target, "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}}


async def _collaboration(ctx: HttpContext, *objects) -> Collaboration:
    """A collaboration of the organization holding ``objects``, as a full member puts them in."""

    def make() -> Collaboration:
        collaboration = Collaboration.objects.create(organization=ctx.request.organization, name="Shared")
        for obj in objects:
            collaboration_items.add(collaboration, obj)
        return collaboration

    return await sync_to_async(make)()


async def _layer(ctx: HttpContext, scene: models.Scene, name: str) -> models.ArrayDataset:
    """An intensity layer on ``scene``, drawing a new dataset."""
    dataset = await seed.create_array_dataset(ctx, name)
    await seed.register_into_scene(ctx, scene, dataset)
    lens = await seed.channel_lens(ctx, await seed.create_lens(ctx, dataset))
    made = await _run(ctx, MAKE_LAYER, input={"scene": str(scene.id), "lens": str(lens.pk)})
    assert not made.errors, made.errors
    return dataset


async def _rendered_scene(ctx: HttpContext, name: str, layers: int = 1) -> tuple[models.Scene, list[models.ArrayDataset]]:
    scene = await seed.create_scene(ctx, name)
    return scene, [await _layer(ctx, scene, f"{name} pixels {index}") for index in range(layers)]


def _refused(result, reason: str) -> None:
    assert result.errors, "it was let through"
    assert result.errors[0].extensions.get("reason") == reason, result.errors


class _Queries:
    """Counts every SQL statement, in whichever thread the ORM runs it."""

    def __init__(self, monkeypatch) -> None:
        self.count = 0
        original = CursorWrapper.execute

        def counted(cursor, sql, params=None):
            self.count += 1
            return original(cursor, sql, params)

        monkeypatch.setattr(CursorWrapper, "execute", counted)

    async def of(self, ctx: HttpContext, query: str, **variables) -> int:
        before = self.count
        result = await _run(ctx, query, **variables)
        assert not result.errors, result.errors
        return self.count - before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_reads_the_scene_in_their_collaboration_and_nothing_else(authenticated_context, guest):
    shared, _ = await _rendered_scene(authenticated_context, "Shared")
    other, _ = await _rendered_scene(authenticated_context, "Other")
    ctx = guest({await _collaboration(authenticated_context, shared): []})

    listed = await _run(ctx, SCENES)
    assert not listed.errors, listed.errors
    assert [s["id"] for s in listed.data["scenes"]] == [str(shared.id)]
    assert (await _run(ctx, SCENE, id=str(shared.id))).data["scene"]["name"] == "Shared"
    assert (await _run(ctx, SCENE, id=str(other.id))).errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_in_a_collaboration_renders_for_a_guest(authenticated_context, guest):
    scene, (dataset,) = await _rendered_scene(authenticated_context, "Shared")
    result = await _run(guest({await _collaboration(authenticated_context, scene): []}), RENDER, id=str(scene.id))
    assert not result.errors, result.errors
    assert result.data["scene"]["worldCoordinateSystem"]["id"] == str(scene.world_id)
    (layer,) = result.data["scene"]["layers"]
    assert layer["lens"]["dataset"]["id"] == str(dataset.id)
    arrays = layer["lens"]["dataset"]["dataArrays"]
    assert arrays
    assert layer["levelPaths"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_added_later_brings_what_it_draws(authenticated_context, guest):
    scene = await seed.create_scene(authenticated_context, "Growing")
    ctx = guest({await _collaboration(authenticated_context, scene): []})
    dataset = await _layer(authenticated_context, scene, "Late pixels")

    result = await _run(ctx, RENDER, id=str(scene.id))
    assert not result.errors, result.errors
    assert result.data["scene"]["layers"][0]["lens"]["dataset"]["id"] == str(dataset.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_what_is_in_no_collaboration_is_left_out(authenticated_context, guest):
    scene, (dataset,) = await _rendered_scene(authenticated_context, "Shared")
    elsewhere = await seed.create_array_dataset(authenticated_context, "Elsewhere")
    ctx = guest({await _collaboration(authenticated_context, scene): []})

    assert (await _run(ctx, "query ($id: ID!) { arrayDataset(id: $id) { id } }", id=str(elsewhere.id))).errors
    seen = await _run(ctx, "query ($id: ID!) { arrayDataset(id: $id) { id } }", id=str(dataset.id))
    assert seen.data["arrayDataset"]["id"] == str(dataset.id)  # brought by the scene
    # Root fields that hand out values computed from many rows are closed to guests.
    _refused(await _run(ctx, "query ($id: ID!) { lineage(start: $id) { __typename } }", id=str(dataset.id)), "COLLABORATION_DOES_NOT_REACH")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_two_scenes_draw_brings_neither_scene_the_others_layers(authenticated_context, guest):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Drawn twice")
    lens = await seed.channel_lens(ctx, await seed.create_lens(ctx, dataset))
    layers = {}
    for name in ("Mine", "Theirs"):
        scene = await seed.create_scene(ctx, name)
        await seed.register_into_scene(ctx, scene, dataset)
        made = await _run(ctx, MAKE_LAYER, input={"scene": str(scene.id), "lens": str(lens.pk)})
        assert not made.errors, made.errors
        layers[name] = made.data["createIntensityLayer"]["id"]
    mine = await sync_to_async(lambda: models.Scene.objects.get(name="Mine"))()
    shared = guest({await _collaboration(ctx, mine, lens): []})  # the lens put in by hand, too

    listed = await _run(shared, "{ layers { id } }")
    assert not listed.errors, listed.errors
    assert [layer["id"] for layer in listed.data["layers"]] == [layers["Mine"]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_editor_in_the_collaboration_draws_there_and_only_there(authenticated_context, guest):
    scene = await seed.create_scene(authenticated_context, "Canvas")
    other = await seed.create_scene(authenticated_context, "Other")
    ctx = guest({await _collaboration(authenticated_context, scene): ["mikro_editor"]})

    drawn = await _run(ctx, DRAW, **_point(scene=str(scene.id)))
    assert not drawn.errors, drawn.errors
    collection = drawn.data["createAnnotation"]["collection"]
    assert collection["scene"]["id"] == str(scene.id)
    again = await _run(ctx, DRAW, **_point(collection=collection["id"]))
    assert not again.errors, again.errors
    listed = await _run(ctx, "{ annotations { id } }")
    assert len(listed.data["annotations"]) == 2  # what they drew is in the collaboration, as part of its collection

    _refused(await _run(ctx, DRAW, **_point(scene=str(other.id))), "SHARE_INSUFFICIENT")
    assert await sync_to_async(models.Annotation.objects.count)() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_viewer_draws_nothing_and_no_guest_does_anything_else(authenticated_context, guest):
    scene = await seed.create_scene(authenticated_context, "Canvas")
    collaboration = await _collaboration(authenticated_context, scene)

    _refused(await _run(guest({collaboration: []}), DRAW, **_point(scene=str(scene.id))), "INSUFFICIENT_ROLE")
    _refused(await _run(guest({collaboration: ["mikro_editor"]}), "mutation ($id: ID!) { deleteScene(id: $id) }", id=str(scene.id)), "SCOPED_READ_ONLY")
    assert await sync_to_async(models.Annotation.objects.count)() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_pays_per_kind_of_object_not_per_row(authenticated_context, guest, monkeypatch):
    small, _ = await _rendered_scene(authenticated_context, "Small", layers=1)
    large, _ = await _rendered_scene(authenticated_context, "Large", layers=3)
    ctx = guest({await _collaboration(authenticated_context, small, large): []})
    member = _context("test")
    queries = _Queries(monkeypatch)

    for scene in (small, large):  # what is read once per process (content types) is read here
        await queries.of(ctx, RENDER, id=str(scene.id))
        await queries.of(member, RENDER, id=str(scene.id))

    async def overhead(scene) -> int:
        guest_cost = await queries.of(ctx, RENDER, id=str(scene.id)) - await queries.of(ctx, SHALLOW, id=str(scene.id))
        member_cost = await queries.of(member, RENDER, id=str(scene.id)) - await queries.of(member, SHALLOW, id=str(scene.id))
        return guest_cost - member_cost

    assert await overhead(small) == await overhead(large)


def _type_line(sdl: str, name: str) -> str:
    return next(line for line in sdl.splitlines() if line.startswith(f"type {name} "))


def test_the_schema_says_what_each_type_is_and_who_may_draw():
    sdl = str(schema)
    assert '@identified(by: "@mikro/scene")' in _type_line(sdl, "Scene")
    create = next(line.strip() for line in sdl.splitlines() if line.strip().startswith("createAnnotation("))
    assert create.endswith('@auth(required_scopes: ["mikro_annotate"], any_role_of: ["mikro_editor", "mikro_annotator"], allow_scoped: true)'), create
