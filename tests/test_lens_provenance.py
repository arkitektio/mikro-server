"""A lens says who made it, when, and under which task -- and is owned by them, or by whoever owns its container.

The same four facts every container carries. The first to ask for a selection is its creator:
asking again hands back the row and changes nothing. A whole lens exists from its container's
creation and carries the container's provenance, because it is the container's handle.
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from arkitekt_datalayer.models import ZarrStore
from mikro_server.schema import schema
from tests import seed
from tests.test_task_provenance import attach_provenance, clear_provenance, make_provenance
from tests.test_windowed_lenses import _CONTAINERS, _create

CROP = [{"axis": "c", "start": 0, "stop": 1}]
CREATE_LENS = "mutation C($input: CreateLensInput!) { createLens(input: $input) { id } }"


async def _lens_row(lens_id: str) -> models.Lens:
    return await models.Lens.objects.select_related("creator", "created_through", "created_through_by").aget(pk=lens_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_is_stamped_with_its_maker_and_the_task_it_was_made_under(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    attach_provenance(ctx, make_provenance(task_id="crop-task"))
    try:
        result = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    finally:
        clear_provenance(ctx)
    assert not result.errors, result.errors

    crop = await _lens_row(result.data["createLens"]["id"])
    assert crop.creator_id == ctx.request.user.id
    assert crop.created_at is not None
    assert crop.created_through is not None and crop.created_through.task_id == "crop-task"
    assert crop.created_through_by_id == crop.created_through.assigner_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_first_to_ask_for_a_selection_is_its_creator(authenticated_context: HttpContext, bot_context: HttpContext):
    """Asking again hands back the row: the second asker becomes nobody's creator, and the row keeps its stamp."""
    dataset = await seed.create_array_dataset(authenticated_context, "Tissue", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    first = await schema.execute(CREATE_LENS, context_value=authenticated_context, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    again = await schema.execute(CREATE_LENS, context_value=bot_context, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    assert not first.errors and not again.errors, (first.errors, again.errors)
    assert first.data["createLens"]["id"] == again.data["createLens"]["id"]
    assert (await _lens_row(first.data["createLens"]["id"])).creator_id == authenticated_context.request.user.id

    make, _, axis = _CONTAINERS["TABLE"]
    table_id, mutation, field = await make(authenticated_context)
    window = [{"axis": axis, "min": 0, "max": 5}]
    mine = await _create(authenticated_context, mutation, field, table_id, window)
    theirs = await _create(bot_context, mutation, field, table_id, window)
    assert mine["id"] == theirs["id"]
    assert (await _lens_row(mine["id"])).creator_id == authenticated_context.request.user.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_lens_carries_its_containers_provenance(authenticated_context: HttpContext):
    """The handle a container is born with says what the container says: maker, time and task."""
    ctx = authenticated_context
    store = await ZarrStore.objects.acreate(organization=ctx.request.organization, key="ingested", bucket="zarr", shape=[3, 64, 64], chunks=[3, 64, 64], version="3", dtype="uint8", populated=True)
    attach_provenance(ctx, make_provenance(task_id="ingest-task"))
    try:
        # Through the mutation: the seed builds its context without a task, and the task is the point.
        with patch("arkitekt_datalayer.models.ZarrStore.fill_info", return_value=None):
            result = await schema.execute(
                "mutation C($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
                context_value=ctx,
                variable_values={"input": {"name": "Ingested", "data": str(store.pk), "scales": [], "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]}},
            )
    finally:
        clear_provenance(ctx)
    assert not result.errors, result.errors
    dataset = await models.ArrayDataset.objects.aget(pk=result.data["createArrayDataset"]["id"])
    whole = await models.ArrayLens.objects.select_related("dataset").aget(dataset=dataset, slices=[])
    assert (whole.creator_id, whole.created_through_id, whole.created_through_by_id) == (dataset.creator_id, dataset.created_through_id, dataset.created_through_by_id)
    assert whole.created_through_id is not None, "the dataset was ingested under a task, so its handle was too"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_a_window_is_stamped_with_whoever_asked_for_it(authenticated_context: HttpContext, bot_context: HttpContext, kind: str):
    """A window over someone else's container belongs to the one who cut it; the container's whole lens to the container's maker."""
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(authenticated_context)
    windowed = await _create(bot_context, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])

    row = await _lens_row(windowed["id"])
    assert row.creator_id == bot_context.request.user.id and row.created_at is not None
    whole = await model.objects.aget(**{model.CONTAINER_FIELD + "_id": container_id}, windows=[])
    container = await sync_to_async(lambda: whole.container)()
    assert whole.creator_id == container.creator_id == authenticated_context.request.user.id


PROVENANCE = """
query Provenance($id: ID!) {
  lens(id: $id) { id createdAt creator { sub } createdThrough { id taskId } createdThroughBy { sub } }
}
"""
UPDATE_LENS = "mutation U($input: UpdateLensInput!) { updateLens(input: $input) { id name } }"
SET_DEFAULT = "mutation S($input: SetLensDefaultSceneInput!) { setLensDefaultScene(input: $input) { id } }"
DELETE_LENS = "mutation D($input: DeleteLensInput!) { deleteLens(input: $input) }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_reports_its_provenance(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    attach_provenance(ctx, make_provenance(task_id="crop-task", sub="1"))
    try:
        made = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    finally:
        clear_provenance(ctx)
    assert not made.errors, made.errors

    read = await schema.execute(PROVENANCE, context_value=ctx, variable_values={"id": made.data["createLens"]["id"]})
    assert not read.errors, read.errors
    lens = read.data["lens"]
    assert lens["creator"]["sub"] == "1" and lens["createdAt"] is not None
    assert lens["createdThrough"]["taskId"] == "crop-task" and lens["createdThroughBy"]["sub"] == "1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_maker_of_a_crop_over_someone_elses_dataset_may_rename_nominate_and_delete_it(authenticated_context: HttpContext, bot_context: HttpContext):
    """A crop is its maker's, whoever owns the pixels under it."""
    dataset = await seed.create_array_dataset(authenticated_context, "Theirs", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    made = await schema.execute(CREATE_LENS, context_value=bot_context, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    assert not made.errors, made.errors
    crop_id = made.data["createLens"]["id"]
    scene = await seed.create_scene(bot_context, "Mine")

    renamed = await schema.execute(UPDATE_LENS, context_value=bot_context, variable_values={"input": {"id": crop_id, "name": "my nucleus"}})
    assert not renamed.errors, renamed.errors
    nominated = await schema.execute(SET_DEFAULT, context_value=bot_context, variable_values={"input": {"lens": crop_id, "scene": str(scene.pk)}})
    assert not nominated.errors, nominated.errors
    deleted = await schema.execute(DELETE_LENS, context_value=bot_context, variable_values={"input": {"id": crop_id}})
    assert not deleted.errors, deleted.errors
    assert not await models.Lens.objects.filter(pk=crop_id).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_containers_owner_may_rename_a_crop_someone_else_made(authenticated_context: HttpContext, bot_context: HttpContext):
    """It is the dataset's cards a lens changes, so the dataset's owner -- not an admin here -- may edit any lens over it."""
    dataset = await seed.create_array_dataset(bot_context, "Bots", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    crop = await seed.create_lens(authenticated_context, dataset, CROP)
    assert crop.creator_id == authenticated_context.request.user.id

    renamed = await schema.execute(UPDATE_LENS, context_value=bot_context, variable_values={"input": {"id": str(crop.pk), "name": "on my data"}})
    assert not renamed.errors, renamed.errors
    assert renamed.data["updateLens"]["name"] == "on my data"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lenses_are_found_by_owner_and_by_task_and_ordered_by_age(authenticated_context: HttpContext, bot_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Tissue", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    attach_provenance(ctx, make_provenance(task_id="find-me", sub="1"))
    try:
        under_task = await schema.execute(CREATE_LENS, context_value=ctx, variable_values={"input": {"dataset": str(dataset.pk), "slices": CROP}})
    finally:
        clear_provenance(ctx)
    assert not under_task.errors, under_task.errors
    task_crop = under_task.data["createLens"]["id"]
    bots = await schema.execute(CREATE_LENS, context_value=bot_context, variable_values={"input": {"dataset": str(dataset.pk), "slices": [{"axis": "y", "start": 0, "stop": 8}]}})
    assert not bots.errors, bots.errors
    bot_crop = bots.data["createLens"]["id"]
    task_id = (await schema.execute(PROVENANCE, context_value=ctx, variable_values={"id": task_crop})).data["lens"]["createdThrough"]["id"]

    async def ids(filters: dict | None = None, ordering: list | None = None) -> list[str]:
        result = await schema.execute(
            "query L($filters: LensFilter, $ordering: [LensOrder!]! = []) { lenses(filters: $filters, ordering: $ordering) { id } }",
            context_value=ctx,
            variable_values={"filters": filters or {"dataset": str(dataset.pk)}, **({"ordering": ordering} if ordering is not None else {})},
        )
        assert not result.errors, result.errors
        return [lens["id"] for lens in result.data["lenses"]]

    assert await ids({"dataset": str(dataset.pk), "owner": "2"}) == [bot_crop], "the bot's crop, not the whole lens or the task's crop"
    assert await ids({"dataset": str(dataset.pk), "createdThrough": task_id}) == [task_crop]
    newest_first = await ids(ordering=[{"createdAt": "DESC"}])
    assert newest_first[0] == bot_crop and newest_first[-1] != bot_crop, "the bot's crop was asked for last"
