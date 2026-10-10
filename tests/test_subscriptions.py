"""The layer, dataset and scene subscriptions, over the transport a client uses.

Nothing here is patched: the mutation writes the row, its receiver broadcasts on the
(in-memory) channel layer once the write is committed, and a real websocket consumer
re-fetches the row through the subscriber's organization and sends the event.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from channels.layers import get_channel_layer
from kante.consumers import KanteWsConsumer
from kante.context import HttpContext
from kante.testing import GraphQLWebSocketTestClient

from core import channels, models
from datalayer.models import ZarrStore
from mikro_server.schema import schema
from tests import seed
from tests.test_windowed_lenses import _CONTAINERS, _create

LAYERS = """
subscription Layers($scene: ID!) {
  layers(scene: $scene) {
    create { __typename id kind opacity visible scene { id } }
    update { __typename id opacity }
    delete
  }
}
"""

ARRAY_DATASETS = """
subscription ArrayDatasets($folder: ID) {
  arrayDatasets(folder: $folder) {
    create { id name folder { id } intrinsicSystem { axes { name } } }
    update { id name }
    delete
  }
}
"""

LENSES = """
subscription Lenses($kind: LensKind, $container: ID) {
  lenses(kind: $kind, container: $container) {
    create { __typename id kind name descriptors ... on ArrayLens { dataset { id } } }
    update { id name }
    delete
  }
}
"""

TABLE_DATASETS = """
subscription TableDatasets($folder: ID) {
  tableDatasets(folder: $folder) {
    create { id name columns { name } }
    update { id name }
    delete
  }
}
"""

SCENES = """
subscription Scenes {
  scenes {
    create { id name }
    update { id preferredView }
    delete
  }
}
"""


async def _next_message(client: GraphQLWebSocketTestClient, timeout: float = 5) -> dict:
    """The next `data` or `error` frame the consumer sent.

    Read off the communicator's queue rather than through ``receive_output``: that one
    cancels the application when it times out, which would end the subscription a test is
    only checking for silence.
    """
    async with asyncio.timeout(timeout):
        while True:
            frame = await client.communicator.output_queue.get()
            if frame["type"] != "websocket.send":
                continue
            message = json.loads(frame["text"])
            if message.get("type") in ("data", "error"):
                return message


class Follower:
    """One started subscription on an open websocket."""

    def __init__(self, client: GraphQLWebSocketTestClient, field: str) -> None:
        self.client = client
        self.field = field

    async def next(self) -> dict:
        """The next event, as the subscription field's value."""
        message = await _next_message(self.client)
        assert message["type"] == "data" and not message["payload"].get("errors"), message
        return message["payload"]["data"][self.field]

    async def silent(self) -> bool:
        """True when nothing arrives: the events of other rooms must not reach this one."""
        try:
            await _next_message(self.client, timeout=0.3)
        except TimeoutError:
            return True
        return False


@asynccontextmanager
async def following(query: str, field: str, room: str, variables: dict | None = None, *, token: str = "test") -> AsyncIterator[Follower]:
    """Subscribe as the bearer of ``token`` and yield once the subscription has joined ``room``.

    Joining is what the wait is for: a change made before the consumer is in the room is
    not replayed, so the test would race its own subscription.
    """
    application = KanteWsConsumer.as_asgi(schema=schema)
    async with GraphQLWebSocketTestClient(application, connection_params={"token": token}) as client:
        start = {"id": "1", "type": "start", "payload": {"query": query, "variables": variables or {}}}
        await client.communicator.send_input({"type": "websocket.receive", "text": json.dumps(start)})
        async with asyncio.timeout(5):
            while not get_channel_layer().groups.get(room):
                await asyncio.sleep(0.01)
        try:
            yield Follower(client, field)
        finally:
            await client.communicator.send_input({"type": "websocket.disconnect", "code": 1000})


async def _refused(query: str, variables: dict, *, token: str) -> dict:
    """Start a subscription that must not be established, and return what came back instead."""
    application = KanteWsConsumer.as_asgi(schema=schema)
    async with GraphQLWebSocketTestClient(application, connection_params={"token": token}) as client:
        start = {"id": "1", "type": "start", "payload": {"query": query, "variables": variables}}
        await client.communicator.send_input({"type": "websocket.receive", "text": json.dumps(start)})
        try:
            return await _next_message(client)
        finally:
            await client.communicator.send_input({"type": "websocket.disconnect", "code": 1000})


async def _run(ctx: HttpContext, query: str, input: dict) -> dict:
    result = await schema.execute(query, context_value=ctx, variable_values={"input": input})
    assert not result.errors, result.errors
    return result.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scenes_layers_are_followed(db, authenticated_context: HttpContext):
    """A layer added to the scene, edited and removed each arrive; another scene's do not."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")
    elsewhere = await seed.create_scene(ctx, "Elsewhere")
    dataset = await seed.create_array_dataset(ctx, "raw")
    lens = await seed.create_lens(ctx, dataset)
    lens = await seed.channel_lens(ctx, lens)
    await seed.register_into_scene(ctx, scene, dataset)
    await seed.register_into_scene(ctx, elsewhere, dataset)

    make = "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }"

    async with following(LAYERS, "layers", channels.scene_layers_room(scene.pk), {"scene": str(scene.pk)}) as follower:
        await _run(ctx, make, {"scene": str(elsewhere.pk), "lens": str(lens.pk)})
        assert await follower.silent(), "a layer of another scene reached this scene's followers"

        layer_id = (await _run(ctx, make, {"scene": str(scene.pk), "lens": str(lens.pk)}))["createIntensityLayer"]["id"]
        event = await follower.next()
        assert event["update"] is None and event["delete"] is None
        # The interface resolves to its concrete type on the re-fetched row.
        assert event["create"]["__typename"] == "IntensityLayer" and event["create"]["kind"] == "INTENSITY"
        assert event["create"]["id"] == layer_id and event["create"]["scene"]["id"] == str(scene.pk)

        await _run(ctx, "mutation U($input: UpdateIntensityLayerInput!) { updateIntensityLayer(input: $input) { id } }", {"id": layer_id, "opacity": 0.25})
        assert await follower.next() == {"create": None, "update": {"__typename": "IntensityLayer", "id": layer_id, "opacity": 0.25}, "delete": None}

        await _run(ctx, "mutation D($input: DeleteLayerInput!) { deleteLayer(input: $input) }", {"id": layer_id})
        assert await follower.next() == {"create": None, "update": None, "delete": layer_id}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clearing_a_scene_tells_its_followers_about_every_layer(db, authenticated_context: HttpContext):
    """`clearScene` deletes through the queryset: still one delete event per layer."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")
    image = await seed.create_array_dataset(ctx, "Drawn")
    lens = await seed.create_lens(ctx, image)
    layers = [await models.Layer.objects.acreate(scene=scene, kind="image", lens=lens) for _ in range(2)]

    async with following(LAYERS, "layers", channels.scene_layers_room(scene.pk), {"scene": str(scene.pk)}) as follower:
        await _run(ctx, "mutation C($input: ClearSceneInput!) { clearScene(input: $input) { id } }", {"id": str(scene.pk)})
        gone = {(await follower.next())["delete"] for _ in layers}
        assert gone == {str(layer.pk) for layer in layers}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_datasets_are_followed_by_folder_and_by_organization(db, authenticated_context: HttpContext):
    """A folder's followers hear that folder only; the organization's hear every folder."""
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Experiment")
    other_folder = await seed.create_folder(ctx, "Other experiment")
    org_room = channels.org_array_datasets_room(ctx.request.organization.id)

    create = "mutation C($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }"

    async def filed(name: str, into: models.Folder) -> str:
        store = await ZarrStore.objects.acreate(organization=ctx.request.organization, key=name, bucket="zarr", shape=[64, 64], chunks=[64, 64], version="3", dtype="uint8", populated=True)
        axes = [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]
        # The one thing not real here: reading the array's metadata would reach for an S3.
        with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
            created = await _run(ctx, create, {"data": str(store.pk), "name": name, "scales": [], "axes": axes, "folder": str(into.pk)})
        return created["createArrayDataset"]["id"]

    update = "mutation U($input: UpdateArrayDatasetInput!) { updateArrayDataset(input: $input) { id } }"
    delete = "mutation D($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }"

    async with (
        following(ARRAY_DATASETS, "arrayDatasets", channels.folder_array_datasets_room(folder.pk), {"folder": str(folder.pk)}) as in_folder,
        following(ARRAY_DATASETS, "arrayDatasets", org_room) as in_org,
    ):
        stray = await filed("stray", other_folder)
        assert (await in_org.next())["create"]["id"] == stray
        assert await in_folder.silent(), "a dataset filed elsewhere reached this folder's followers"

        dataset = await filed("raw", folder)
        for follower in (in_folder, in_org):
            # Whole on arrival: the axes are written after the dataset row, in its transaction.
            expected = {"id": dataset, "name": "raw", "folder": {"id": str(folder.pk)}, "intrinsicSystem": {"axes": [{"name": "y"}, {"name": "x"}]}}
            assert await follower.next() == {"create": expected, "update": None, "delete": None}

        await _run(ctx, update, {"id": dataset, "name": "nuclei"})
        for follower in (in_folder, in_org):
            assert await follower.next() == {"create": None, "update": {"id": dataset, "name": "nuclei"}, "delete": None}

        await _run(ctx, delete, {"id": dataset})
        for follower in (in_folder, in_org):
            assert await follower.next() == {"create": None, "update": None, "delete": dataset}
            assert await follower.silent()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_table_datasets_are_followed(db, authenticated_context: HttpContext):
    """A created table arrives whole -- its columns are written in the same transaction, and the event waits for it."""
    ctx = authenticated_context
    columns = [{"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "micrometer"}, {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"}]
    create = "mutation C($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id folder { id } } }"
    payload = await seed.table_input(ctx, "cells", columns)

    async with following(TABLE_DATASETS, "tableDatasets", channels.org_table_datasets_room(ctx.request.organization.id)) as follower:
        created = (await _run(ctx, create, payload))["createTableDataset"]
        event = await follower.next()
        assert event["create"]["id"] == created["id"] and event["create"]["name"] == "cells"
        assert {column["name"] for column in event["create"]["columns"]} == {"x", "area"}
        # One event for one create, although the mutation saves the row twice.
        assert await follower.silent()

    # The same table, renamed and deleted, as its folder's followers see it.
    folder_id = created["folder"]["id"]
    async with following(TABLE_DATASETS, "tableDatasets", channels.folder_table_datasets_room(folder_id), {"folder": folder_id}) as follower:
        await _run(ctx, "mutation U($input: UpdateTableDatasetInput!) { updateTableDataset(input: $input) { id } }", {"id": created["id"], "name": "nuclei"})
        assert await follower.next() == {"create": None, "update": {"id": created["id"], "name": "nuclei"}, "delete": None}

        await _run(ctx, "mutation D($input: DeleteTableDatasetInput!) { deleteTableDataset(input: $input) }", {"id": created["id"]})
        assert await follower.next() == {"create": None, "update": None, "delete": created["id"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scenes_are_followed(db, authenticated_context: HttpContext):
    ctx = authenticated_context

    async with following(SCENES, "scenes", channels.org_scenes_room(ctx.request.organization.id)) as follower:
        scene_id = (await _run(ctx, "mutation C($input: CreateSceneInput!) { createScene(input: $input) { id } }", {"name": "Canvas"}))["createScene"]["id"]
        assert await follower.next() == {"create": {"id": scene_id, "name": "Canvas"}, "update": None, "delete": None}

        await _run(ctx, "mutation U($input: UpdateSceneInput!) { updateScene(input: $input) { id } }", {"id": scene_id, "preferredView": "THREE_D"})
        assert await follower.next() == {"create": None, "update": {"id": scene_id, "preferredView": "THREE_D"}, "delete": None}

        await _run(ctx, "mutation D($input: DeleteSceneInput!) { deleteScene(input: $input) }", {"id": scene_id})
        assert await follower.next() == {"create": None, "update": None, "delete": scene_id}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_datasets_lenses_are_followed(db, authenticated_context: HttpContext):
    """A dataset's followers hear its crops only; the organization's hear every container's."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Followed", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    other = await seed.create_array_dataset(ctx, "Other", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    org_room = channels.org_lenses_room(ctx.request.organization.id)

    create = "mutation C($input: CreateLensInput!) { createLens(input: $input) { id } }"
    rename = "mutation R($input: UpdateLensInput!) { updateLens(input: $input) { id } }"
    delete = "mutation D($input: DeleteLensInput!) { deleteLens(input: $input) }"
    crop = [{"axis": "c", "start": 0, "stop": 1}]

    async with (
        following(LENSES, "lenses", channels.container_lenses_room("ARRAY", dataset.pk), {"kind": "ARRAY", "container": str(dataset.pk)}) as of_dataset,
        following(LENSES, "lenses", org_room) as in_org,
    ):
        stray = (await _run(ctx, create, {"dataset": str(other.pk), "slices": crop}))["createLens"]["id"]
        assert (await in_org.next())["create"]["id"] == stray
        assert await of_dataset.silent(), "a crop of another dataset reached this dataset's followers"

        lens = (await _run(ctx, create, {"dataset": str(dataset.pk), "slices": crop}))["createLens"]["id"]
        for follower in (of_dataset, in_org):
            event = await follower.next()
            assert event["update"] is None and event["delete"] is None
            created = event["create"]
            assert (created["__typename"], created["id"], created["kind"], created["name"], created["dataset"]["id"]) == ("ArrayLens", lens, "ARRAY", None, str(dataset.pk))
            assert created["descriptors"]["@mikro/is_whole"] is False and created["descriptors"]["@mikro/n_channels"] == 1

        await _run(ctx, rename, {"id": lens, "name": "nucleus"})
        for follower in (of_dataset, in_org):
            assert await follower.next() == {"create": None, "update": {"id": lens, "name": "nucleus"}, "delete": None}

        await _run(ctx, delete, {"id": lens})
        for follower in (of_dataset, in_org):
            assert await follower.next() == {"create": None, "update": None, "delete": lens}
            assert await follower.silent()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_a_windowed_lens_is_followed(db, authenticated_context: HttpContext, kind: str):
    """A container of any kind can be followed; a window over it arrives as its kind."""
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)

    async with following(LENSES, "lenses", channels.container_lenses_room(kind, container_id), {"kind": kind, "container": container_id}) as follower:
        windowed = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])
        event = await follower.next()
        assert (event["create"]["id"], event["create"]["kind"], event["create"]["__typename"]) == (windowed["id"], kind, model.__name__)
        assert event["create"]["descriptors"]["@mikro/is_whole"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_container_tells_its_lens_followers(db, authenticated_context: HttpContext):
    """The cascade announces every lens the container took with it: the whole lens and the crop."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Doomed", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    whole = await models.ArrayLens.objects.aget(dataset=dataset, slices=[])
    crop = await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}])

    async with following(LENSES, "lenses", channels.container_lenses_room("ARRAY", dataset.pk), {"kind": "ARRAY", "container": str(dataset.pk)}) as follower:
        await _run(ctx, "mutation D($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }", {"id": str(dataset.pk)})
        gone = {(await follower.next())["delete"], (await follower.next())["delete"]}
        assert gone == {str(whole.pk), str(crop.pk)}
        assert await follower.silent()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_organization_hears_nothing(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """Org-wide followers of another organization are in another room; a foreign scene or folder cannot be followed at all."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")
    folder = await seed.create_folder(ctx, "Experiment")

    async with following(SCENES, "scenes", channels.org_scenes_room(other_org_context.request.organization.id), token="othertest") as theirs:
        await _run(ctx, "mutation C($input: CreateSceneInput!) { createScene(input: $input) { id } }", {"name": "Ours"})
        assert await theirs.silent(), "a scene of one organization reached another's followers"

    dataset = await seed.create_array_dataset(ctx, "Theirs")
    for query, variables in ((LAYERS, {"scene": str(scene.pk)}), (ARRAY_DATASETS, {"folder": str(folder.pk)}), (TABLE_DATASETS, {"folder": str(folder.pk)}), (LENSES, {"kind": "ARRAY", "container": str(dataset.pk)})):
        refusal = await _refused(query, variables, token="othertest")
        assert "does not exist in this organization" in json.dumps(refusal), refusal


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rolled_back_change_is_never_announced(db, authenticated_context: HttpContext):
    """Events wait for the commit: a write that rolls back tells nobody."""
    from asgiref.sync import sync_to_async
    from django.db import transaction

    ctx = authenticated_context
    world = (await seed.create_scene(ctx, "Seed")).world_id

    def write_and_roll_back() -> None:
        with transaction.atomic():
            models.Scene.objects.create(name="Never", world_id=world, organization=ctx.request.organization)
            transaction.set_rollback(True)

    async with following(SCENES, "scenes", channels.org_scenes_room(ctx.request.organization.id)) as follower:
        await sync_to_async(write_and_roll_back)()
        assert await follower.silent()
