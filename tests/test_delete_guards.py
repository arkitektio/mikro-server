"""Ownership guards on delete mutations.

Deletion of user-owned rows is guarded (``core.mutations._generic``): a caller
may delete a row only if they are its ``creator`` or its ``created_through_by``
assigner, unless they hold the ``admin`` role in the organization.

Because every static test token defaults to ``roles=["admin"]``, the denial
path is exercised through ``bot_context`` — a same-org user holding only the
``bot`` role, which passes the admin/bot mutation gate but is not an admin, so
the guard applies to it.
"""

import pytest

from core import models
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed
from tests.seed import create_folder, create_other_user


# --- creator / assignee / admin guard on a self-owned model (Folder) --------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_creator_can_delete_own_folder(db, authenticated_context: HttpContext):
    folder = await create_folder(authenticated_context, "Mine")

    mutation = "mutation($id: ID!) { deleteFolder(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Folder.objects.filter(id=folder.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_owner_non_admin_cannot_delete_folder(db, authenticated_context: HttpContext, bot_context: HttpContext):
    # Owned by the admin user (sub 1); the bot user (sub 2, same org) is neither
    # creator nor assignee nor admin.
    folder = await create_folder(authenticated_context, "NotYours")

    mutation = "mutation($id: ID!) { deleteFolder(input: {id: $id}) }"
    denied = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=bot_context)

    assert denied.errors, "a non-owner non-admin user could delete the folder"
    assert await models.Folder.objects.filter(id=folder.pk).aexists()

    # The creator can still delete it.
    allowed = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=authenticated_context)
    assert not allowed.errors, allowed.errors
    assert not await models.Folder.objects.filter(id=folder.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_org_admin_can_delete_other_users_folder(db, authenticated_context: HttpContext):
    # Created by a second user (sub 2); deleted by the admin user (sub 1).
    bot_user = await create_other_user(authenticated_context)
    folder = await create_folder(authenticated_context, "BotsFolder", creator=bot_user)

    mutation = "mutation($id: ID!) { deleteFolder(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Folder.objects.filter(id=folder.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_assignee_can_delete_folder(db, authenticated_context: HttpContext, bot_context: HttpContext):
    # Creator is the admin user, but the bot user is the assigner
    # (created_through_by): the assignee path lets the bot delete it.
    bot_user = await create_other_user(authenticated_context)
    folder = await create_folder(authenticated_context, "Assigned", created_through_by=bot_user)

    mutation = "mutation($id: ID!) { deleteFolder(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=bot_context)

    assert not result.errors, result.errors
    assert not await models.Folder.objects.filter(id=folder.pk).aexists()


# --- newly added delete mutations (wired + delete) ---------------------------


async def _seed_array_dataset(ctx: HttpContext, *, creator=None) -> models.ArrayDataset:
    dataset = await seed.create_array_dataset(ctx, "ADS", shapes=[[1, 32, 32]])
    if creator is None:
        dataset.creator = None
        await dataset.asave(update_fields=["creator"])
    return dataset


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_array_dataset(db, authenticated_context: HttpContext):
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)

    mutation = "mutation($id: ID!) { deleteArrayDataset(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(array_dataset.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.ArrayDataset.objects.filter(id=array_dataset.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_array_dataset_denied_for_non_owner(db, authenticated_context: HttpContext, bot_context: HttpContext):
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)

    mutation = "mutation($id: ID!) { deleteArrayDataset(input: {id: $id}) }"
    denied = await schema.execute(mutation, variable_values={"id": str(array_dataset.pk)}, context_value=bot_context)

    assert denied.errors, "a non-owner non-admin user could delete the array dataset"
    assert await models.ArrayDataset.objects.filter(id=array_dataset.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_data_array(db, authenticated_context: HttpContext):
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    # Level 1, not 0: the seed already created level 0, and (dataset, level) is
    # unique -- two arrays claiming the same level would make "the level-0 array"
    # ambiguous everywhere.
    data_array = await models.DataArray.objects.acreate(
        level=1, dataset=array_dataset, shape=[1, 32, 32], chunk_shape=[1, 32, 32]
    )

    mutation = "mutation($id: ID!) { deleteDataArray(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(data_array.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.DataArray.objects.filter(id=data_array.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_lens(db, authenticated_context: HttpContext):
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    lens = await models.Lens.objects.acreate(dataset=array_dataset, slices=[])

    mutation = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(lens.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Lens.objects.filter(id=lens.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_scene(db, authenticated_context: HttpContext):
    scene = await seed.create_scene(authenticated_context)

    mutation = "mutation($id: ID!) { deleteScene(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(scene.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Scene.objects.filter(id=scene.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_layer(db, authenticated_context: HttpContext):
    scene = await seed.create_scene(authenticated_context)
    layer = await models.Layer.objects.acreate(scene=scene)

    mutation = "mutation($id: ID!) { deleteLayer(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(layer.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Layer.objects.filter(id=layer.pk).aexists()
