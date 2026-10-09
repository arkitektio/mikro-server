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
from datalayer.models import ZarrStore
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
        with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
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
