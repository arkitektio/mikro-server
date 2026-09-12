"""A snapshot is a picture of a composition, and it belongs to the scene it depicts.

There is no picture of a dataset -- only pictures of scenes. A dataset previews itself through
the scene it **nominates** as its `defaultScene`, and that nomination is the thing these pin.

This replaced a derivation. `latestSnapshot` used to answer from *sole occupancy*: the newest
picture of a scene whose only anchored dataset was this one. That guaranteed the tile showed
nothing else, and it cost a five-query graph walk per request and returned null for every
dataset staged alongside another -- so a gallery of datasets composed together showed no tiles
at all. A nomination is cheaper (one local column) and answers in the case that actually
mattered, at the honest cost that a shared scene's tile shows the other data too.

These also pin the three places this deliberately departs from the legacy `Snapshot` it
mirrors, each of which is a live bug there: the row records its creator (so the delete
guard has someone to match), the store is non-null (so the non-null `store` field cannot
fail at runtime), and the list query is organization-scoped (so it cannot serve another
org's thumbnails).
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from datalayer.models import MediaStore
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: SceneSnapshotInput!) {
  createSceneSnapshot(input: $input) {
    id
    name
    scene { id }
    store { id }
  }
}
"""

DELETE = """
mutation Delete($input: DeleteSceneSnapshotInput!) {
  deleteSceneSnapshot(input: $input)
}
"""

PIN = """
mutation Pin($input: PinSceneSnapshotInput!) {
  pinSceneSnapshot(input: $input) { id }
}
"""

LIST = """
query List($filters: SceneSnapshotFilter) {
  sceneSnapshots(filters: $filters) { id name }
}
"""

SET_DEFAULT = """
mutation SetDefault($input: SetDefaultSceneInput!) {
  setDefaultScene(input: $input) { id defaultScene { id name } }
}
"""

LATEST = """
query Latest($dataset: ID!, $lens: ID!, $scene: ID!) {
  arrayDataset(id: $dataset) { latestSnapshot { name } }
  lens(id: $lens) { latestSnapshot { name } }
  scene(id: $scene) { latestSnapshot { name } }
}
"""


async def _media_store(ctx: HttpContext, key: str) -> MediaStore:
    """A populated media store, as `finishMediaUpload` would leave it."""
    return await MediaStore.objects.acreate(
        organization=ctx.request.organization,
        path=f"s3://media/{key}",
        key=key,
        bucket="media",
        content_type="image/png",
        populated=True,
    )


async def _snapshot(ctx: HttpContext, scene, key: str, name=None):
    store = await _media_store(ctx, key)
    payload = {"file": str(store.pk), "scene": str(scene.pk)}
    if name is not None:
        payload["name"] = name
    result = await schema.execute(CREATE, context_value=ctx, variable_values={"input": payload})
    assert not result.errors, result.errors
    return result.data["createSceneSnapshot"]


async def _nominate(ctx: HttpContext, dataset, scene) -> None:
    """Point a dataset at a scene through the real mutation."""
    result = await schema.execute(
        SET_DEFAULT,
        context_value=ctx,
        variable_values={"input": {"dataset": str(dataset.pk), "scene": str(scene.pk)}},
    )
    assert not result.errors, result.errors


async def _names(ctx: HttpContext, filters: dict) -> set[str]:
    result = await schema.execute(LIST, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {row["name"] for row in result.data["sceneSnapshots"]}


async def _latest(ctx: HttpContext, dataset, lens, scene):
    """(array_dataset.latestSnapshot, lens.latestSnapshot, scene.latestSnapshot) names, or None.

    The latest-snapshot map is per-request state, cached on the context's loader store.
    Production builds a fresh HttpContext per request; this fixture hands the *same* one to
    every execute(), so the store is dropped here to make each call a real request. Without
    this a test that snapshots between two calls would read the first call's map and pass
    while proving nothing.
    """
    ctx._loaders.pop("latest_snapshot_by_scene", None)
    result = await schema.execute(
        LATEST,
        context_value=ctx,
        variable_values={"dataset": str(dataset.pk), "lens": str(lens.pk), "scene": str(scene.pk)},
    )
    assert not result.errors, result.errors
    return tuple((result.data[key]["latestSnapshot"] or {}).get("name") for key in ("arrayDataset", "lens", "scene"))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _snapshot(ctx, scene, "shot.png", name="Shot")
    assert created["scene"]["id"] == str(scene.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_records_its_creator(db, authenticated_context: HttpContext):
    """The legacy `create_snapshot` never sets `creator`, which leaves its own delete guard
    matching nobody but admins. This one sets it, and that is what makes the guard real."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _snapshot(ctx, scene, "creator.png")

    row = await models.SceneSnapshot.objects.aget(pk=created["id"])
    assert row.creator_id == ctx.request.user.id
    assert row.organization_id == ctx.request.organization.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_scene_deletes_its_pictures(db, authenticated_context: HttpContext):
    """A picture of a composition depicts nothing once the composition is gone."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _snapshot(ctx, scene, "gone.png")

    await sync_to_async(scene.delete)()
    assert not await models.SceneSnapshot.objects.filter(pk=created["id"]).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filters(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    first = await seed.create_scene(ctx, "First")
    second = await seed.create_scene(ctx, "Second")
    third = await seed.create_scene(ctx, "Third")

    await _snapshot(ctx, first, "a.png", name="A")
    await _snapshot(ctx, second, "b.png", name="B")
    await _snapshot(ctx, third, "c.png", name="C")

    assert await _names(ctx, {"scene": str(first.pk)}) == {"A"}
    assert await _names(ctx, {"scenes": [str(first.pk), str(second.pk)]}) == {"A", "B"}
    assert await _names(ctx, {"search": "a"}) == {"A"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pin_toggles_both_ways(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    created = await _snapshot(ctx, scene, "pin.png", name="Pinned")

    result = await schema.execute(PIN, context_value=ctx, variable_values={"input": {"id": created["id"], "pin": True}})
    assert not result.errors, result.errors
    assert await _names(ctx, {"pinned": True}) == {"Pinned"}

    result = await schema.execute(PIN, context_value=ctx, variable_values={"input": {"id": created["id"], "pin": False}})
    assert not result.errors, result.errors
    assert await _names(ctx, {"pinned": True}) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_guard(db, authenticated_context: HttpContext, bot_context: HttpContext):
    """Run as the bot, not the default context.

    `assert_can_delete` returns early for org admins, and `authenticated_context` is one --
    so a delete test written with it exercises the short-circuit and never the guard.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")

    mine = await _snapshot(bot_context, scene, "mine.png", name="Mine")
    result = await schema.execute(DELETE, context_value=bot_context, variable_values={"input": {"id": mine["id"]}})
    assert not result.errors, result.errors
    assert not await models.SceneSnapshot.objects.filter(pk=mine["id"]).aexists()

    theirs = await _snapshot(ctx, scene, "theirs.png", name="Theirs")
    result = await schema.execute(DELETE, context_value=bot_context, variable_values={"input": {"id": theirs["id"]}})
    assert result.errors, "a non-admin must not delete another user's snapshot"
    assert await models.SceneSnapshot.objects.filter(pk=theirs["id"]).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_list_is_scoped_to_the_organization(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """Without get_queryset a bare list field returns every row in the table.

    The legacy `snapshots` field has exactly that hole. It needs two organizations to see,
    and the rest of the suite is single-org -- so nothing else here would catch it.
    """
    ctx = authenticated_context
    await _snapshot(ctx, await seed.create_scene(ctx, "Ours"), "ours.png", name="Ours")
    await _snapshot(other_org_context, await seed.create_scene(other_org_context, "Theirs"), "theirs.png", name="Theirs")

    assert await _names(ctx, {}) == {"Ours"}
    assert await _names(other_org_context, {}) == {"Theirs"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_previews_the_scene_it_nominates(db, authenticated_context: HttpContext):
    """The whole feature: nominate a scene, and the dataset reports that scene's newest picture."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")
    lens = await seed.create_lens(ctx, dataset, slices=[])
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, dataset)

    await _snapshot(ctx, scene, "old.png", name="Old")
    await _snapshot(ctx, scene, "new.png", name="New")

    # Registration alone is not a nomination -- the scene has a picture, the dataset has none.
    assert await _latest(ctx, dataset, lens, scene) == (None, None, "New")

    await _nominate(ctx, dataset, scene)
    assert await _latest(ctx, dataset, lens, scene) == ("New", "New", "New")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_second_dataset_in_the_scene_no_longer_blinds_it(db, authenticated_context: HttpContext):
    """The inversion, and the reason for the change.

    This replaces `test_sole_occupancy_a_second_dataset_blinds_both`, which pinned the opposite:
    a scene holding two datasets was a preview of neither, so both went blank. That rule made a
    gallery of datasets staged together show no tiles at all. A nomination is a person saying
    "this one", so a shared scene is a perfectly good answer for both -- with the honest cost
    that the tile shows the other dataset too, which the field description now states.
    """
    ctx = authenticated_context
    first = await seed.create_array_dataset(ctx, "First")
    second = await seed.create_array_dataset(ctx, "Second")
    first_lens = await seed.create_lens(ctx, first, slices=[])
    second_lens = await seed.create_lens(ctx, second, slices=[])
    scene = await seed.create_scene(ctx, "Shared")

    await seed.register_into_scene(ctx, scene, first)
    await seed.register_into_scene(ctx, scene, second)
    await _snapshot(ctx, scene, "both.png", name="Both")

    await _nominate(ctx, first, scene)
    await _nominate(ctx, second, scene)

    assert await _latest(ctx, first, first_lens, scene) == ("Both", "Both", "Both")
    assert await _latest(ctx, second, second_lens, scene) == ("Both", "Both", "Both")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_scene_is_the_default_for_many_datasets(db, authenticated_context: HttpContext):
    """The shape the dataset-side FK exists to allow, read from the scene."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Plate")
    datasets = [await seed.create_array_dataset(ctx, f"Well{i}") for i in range(3)]
    for dataset in datasets:
        await _nominate(ctx, dataset, scene)

    result = await schema.execute(
        "query S($id: ID!) { scene(id: $id) { defaultFor { name } } }",
        context_value=ctx,
        variable_values={"id": str(scene.pk)},
    )
    assert not result.errors, result.errors
    assert {row["name"] for row in result.data["scene"]["defaultFor"]} == {"Well0", "Well1", "Well2"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_latest_falls_back_to_the_previous(db, authenticated_context: HttpContext):
    """Latest is recomputed per request, never stored -- deleting it needs no invalidation."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")
    lens = await seed.create_lens(ctx, dataset, slices=[])
    scene = await seed.create_scene(ctx, "Composition")
    await _nominate(ctx, dataset, scene)

    await _snapshot(ctx, scene, "old.png", name="Old")
    newest = await _snapshot(ctx, scene, "new.png", name="New")
    assert await _latest(ctx, dataset, lens, scene) == ("New", "New", "New")

    result = await schema.execute(DELETE, context_value=ctx, variable_values={"input": {"id": newest["id"]}})
    assert not result.errors, result.errors
    assert await _latest(ctx, dataset, lens, scene) == ("Old", "Old", "Old")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_nominating_nothing_has_no_latest_snapshot(db, authenticated_context: HttpContext):
    """No nomination, no tile -- even for a scene the dataset is registered into.

    This is the deliberate regression the `backfill_default_scenes` command exists to absorb:
    before, registration alone was enough.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Unnominated")
    lens = await seed.create_lens(ctx, dataset, slices=[])
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, dataset)
    await _snapshot(ctx, scene, "elsewhere.png", name="Elsewhere")

    dataset_latest, lens_latest, scene_latest = await _latest(ctx, dataset, lens, scene)
    assert dataset_latest is None
    assert lens_latest is None
    assert scene_latest == "Elsewhere", "the scene still has its own picture"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_lens_of_a_dataset_reports_its_default(db, authenticated_context: HttpContext):
    """The nomination is a fact about the dataset, so its lenses all answer alike."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")
    whole = await seed.create_lens(ctx, dataset, slices=[])
    cropped = await seed.create_lens(ctx, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    scene = await seed.create_scene(ctx, "Composition")
    await _nominate(ctx, dataset, scene)
    await _snapshot(ctx, scene, "both.png", name="Both")

    assert await _latest(ctx, dataset, whole, scene) == ("Both", "Both", "Both")
    assert await _latest(ctx, dataset, cropped, scene) == ("Both", "Both", "Both")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_scene_clears_the_nomination(db, authenticated_context: HttpContext):
    """SET_NULL: deleting a scene must never delete or hide the data that pointed at it."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")
    scene = await seed.create_scene(ctx, "Doomed")
    await _nominate(ctx, dataset, scene)

    result = await schema.execute(
        "mutation D($input: DeleteSceneInput!) { deleteScene(input: $input) }",
        context_value=ctx,
        variable_values={"input": {"id": str(scene.pk)}},
    )
    assert not result.errors, result.errors

    refreshed = await models.ArrayDataset.objects.aget(pk=dataset.pk)
    assert refreshed.default_scene_id is None, "the pointer clears"
    assert await models.ArrayDataset.objects.filter(pk=dataset.pk).aexists(), "and the dataset survives"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_set_default_scene_clears_with_null(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")
    scene = await seed.create_scene(ctx, "Composition")
    await _nominate(ctx, dataset, scene)

    result = await schema.execute(
        SET_DEFAULT,
        context_value=ctx,
        variable_values={"input": {"dataset": str(dataset.pk), "scene": None}},
    )
    assert not result.errors, result.errors
    assert result.data["setDefaultScene"]["defaultScene"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_default_for_at_scene_creation(db, authenticated_context: HttpContext):
    """The reverse setup: stage a space and point its data back at the result in one call."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "DS")

    result = await schema.execute(
        """
        mutation C($input: CreateSceneInput!) {
          createScene(input: $input) { id defaultFor { name } }
        }
        """,
        context_value=ctx,
        variable_values={"input": {"name": "Staged", "defaultFor": [str(dataset.pk)]}},
    )
    assert not result.errors, result.errors
    assert [row["name"] for row in result.data["createScene"]["defaultFor"]] == ["DS"]

    refreshed = await models.ArrayDataset.objects.aget(pk=dataset.pk)
    assert str(refreshed.default_scene_id) == result.data["createScene"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_datasets_owner_may_nominate(db, authenticated_context: HttpContext, bot_context: HttpContext):
    """Guarded on the dataset, because `Scene` carries no creator to guard on."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Theirs")
    scene = await seed.create_scene(ctx, "Composition")

    result = await schema.execute(
        SET_DEFAULT,
        context_value=bot_context,
        variable_values={"input": {"dataset": str(dataset.pk), "scene": str(scene.pk)}},
    )
    assert result.errors, "a non-owner must not repoint another user's thumbnail"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_datasets_can_be_filtered_by_whether_they_have_a_default(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    nominated = await seed.create_array_dataset(ctx, "Nominated")
    await seed.create_array_dataset(ctx, "Bare")
    scene = await seed.create_scene(ctx, "Composition")
    await _nominate(ctx, nominated, scene)

    async def names(filters):
        result = await schema.execute(
            "query L($filters: ArrayDatasetFilter) { arrayDatasets(filters: $filters) { name } }",
            context_value=ctx,
            variable_values={"filters": filters},
        )
        assert not result.errors, result.errors
        return {row["name"] for row in result.data["arrayDatasets"]}

    assert await names({"hasDefaultScene": True}) == {"Nominated"}
    assert await names({"hasDefaultScene": False}) == {"Bare"}

    result = await schema.execute(
        "query S($filters: SceneFilter) { scenes(filters: $filters) { name } }",
        context_value=ctx,
        variable_values={"filters": {"defaultForDataset": str(nominated.pk)}},
    )
    assert not result.errors, result.errors
    assert [row["name"] for row in result.data["scenes"]] == ["Composition"]


LIST_WITH_LATEST = """
query {
  arrayDatasets { id latestSnapshot { id store { key } } }
  scenes { id latestSnapshot { id store { key } } }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_latest_snapshot_page_is_constant_queries(db, authenticated_context: HttpContext, monkeypatch):
    """A page of datasets and scenes must not pay per row for its tiles.

    Two regressions, one bound. `latestSnapshot` once walked the placement graph per org scene
    (~15 queries each -- 8 datasets cost ~130); a later sole-occupancy map cut that to a flat
    five queries plus one DISTINCT ON, measured at **16** for this page. Reading a nominated
    scene drops the five: `default_scene_id` is a column on a row already fetched.

    The threshold is tightened accordingly. Leaving it at the old 30 would have let the whole
    point of the change go unmeasured. Counted by patching the cursor, not
    `CaptureQueriesContext`, because resolvers run on executor threads with their own
    connections.
    """
    ctx = authenticated_context
    for i in range(8):
        dataset = await seed.create_array_dataset(ctx, f"DS{i}")
        scene = await seed.create_scene(ctx, f"Scene{i}")
        await seed.register_into_scene(ctx, scene, dataset)
        await _snapshot(ctx, scene, f"shot{i}.png", name=f"Shot{i}")
        await _nominate(ctx, dataset, scene)

    from django.db.backends import utils as db_utils

    queries: list[str] = []
    original = db_utils.CursorWrapper.execute

    def counting_execute(cursor_self, sql, params=None):
        queries.append(sql)
        return original(cursor_self, sql, params)

    monkeypatch.setattr(db_utils.CursorWrapper, "execute", counting_execute)
    ctx._loaders.clear()

    result = await schema.execute(LIST_WITH_LATEST, context_value=ctx)
    assert not result.errors, result.errors
    tiles = {row["latestSnapshot"]["id"] for row in result.data["arrayDatasets"] if row["latestSnapshot"]}
    assert len(tiles) == 8, "every dataset must report its own tile"
    assert len(queries) <= 12, f"page ran {len(queries)} queries -- was 16 with the sole-occupancy walk, and a per-row cost would be far worse:\n" + "\n".join(queries)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_latest_snapshot_does_not_cross_organizations(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """The candidate scenes are org-scoped, so another org's composition is never the answer."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Ours")
    lens = await seed.create_lens(ctx, dataset, slices=[])
    scene = await seed.create_scene(ctx, "Ours")
    await seed.register_into_scene(ctx, scene, dataset)

    their_scene = await seed.create_scene(other_org_context, "Theirs")
    await _snapshot(other_org_context, their_scene, "theirs.png", name="Theirs")

    assert await _latest(ctx, dataset, lens, scene) == (None, None, None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_backfill_seeds_defaults_from_the_old_sole_occupancy_rule(db, authenticated_context: HttpContext):
    """The command that stops every existing thumbnail vanishing on deploy.

    It reproduces the derivation `latestSnapshot` used to do -- sole occupancy -- once, into
    the new column. A dataset sharing its scene with another was never given a tile by that
    rule, so it gets none here either; it is exactly the set the old field could answer for.
    """
    from io import StringIO

    from django.core.management import call_command

    ctx = authenticated_context
    sole = await seed.create_array_dataset(ctx, "Sole")
    lens = await seed.create_lens(ctx, sole, slices=[])
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, sole)
    await _snapshot(ctx, scene, "sole.png", name="Sole")

    # Shares a scene with another dataset: sole occupancy never answered for these.
    shared_a = await seed.create_array_dataset(ctx, "SharedA")
    shared_b = await seed.create_array_dataset(ctx, "SharedB")
    shared_scene = await seed.create_scene(ctx, "Shared")
    await seed.register_into_scene(ctx, shared_scene, shared_a)
    await seed.register_into_scene(ctx, shared_scene, shared_b)

    # Already nominated by hand: the backfill must not overwrite a deliberate choice.
    chosen = await seed.create_array_dataset(ctx, "Chosen")
    chosen_scene = await seed.create_scene(ctx, "Chosen")
    await seed.register_into_scene(ctx, chosen_scene, chosen)
    await _nominate(ctx, chosen, shared_scene)

    assert await _latest(ctx, sole, lens, scene) == (None, None, "Sole"), "no default yet"

    out = StringIO()
    await sync_to_async(call_command)("backfill_default_scenes", stdout=out, stderr=out)

    assert await _latest(ctx, sole, lens, scene) == ("Sole", "Sole", "Sole"), "the old answer, now stored"

    refreshed_chosen = await models.ArrayDataset.objects.aget(pk=chosen.pk)
    assert refreshed_chosen.default_scene_id == shared_scene.pk, "a hand-set nomination outranks the backfill"

    for dataset in (shared_a, shared_b):
        refreshed = await models.ArrayDataset.objects.aget(pk=dataset.pk)
        assert refreshed.default_scene_id is None, "sole occupancy never answered for a shared scene"

    assert "still have no default scene" in out.getvalue(), "the finish line must be reported"
