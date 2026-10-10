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
async def test_delete_lens(db, authenticated_context: HttpContext):
    """A sliced lens nothing draws through can go."""
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    lens = await seed.create_lens(authenticated_context, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])

    mutation = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(lens.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Lens.objects.filter(id=lens.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_crop_takes_its_own_space_and_edge(db, authenticated_context: HttpContext):
    """A crop owns the space it re-indexes into and the edge back to the grid; nothing else is attached, so both go with it."""
    ctx = authenticated_context
    array_dataset = await _seed_array_dataset(ctx, creator=ctx.request.user)
    systems_before = await models.CoordinateSystem.objects.acount()
    edges_before = await models.Transformation.objects.acount()
    crop = await seed.create_lens(ctx, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])
    assert await models.CoordinateSystem.objects.acount() == systems_before + 1 and await models.Transformation.objects.acount() == edges_before + 1

    result = await schema.execute("mutation($id: ID!) { deleteLens(input: {id: $id}) }", variable_values={"id": str(crop.pk)}, context_value=ctx)
    assert not result.errors, result.errors

    assert not await models.CoordinateSystem.objects.filter(pk=crop.coordinate_system_id).aexists(), "the crop's space went with it"
    assert await models.CoordinateSystem.objects.acount() == systems_before and await models.Transformation.objects.acount() == edges_before
    assert await models.CoordinateSystem.objects.filter(pk=array_dataset.coordinate_system_id).aexists(), "the grid it was cut from stays"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_whose_space_is_registered_elsewhere_leaves_the_space_behind(db, authenticated_context: HttpContext):
    """Someone built on the crop's space -- registered it into another -- so the space outlives the crop, with the edges it had."""
    ctx = authenticated_context
    array_dataset = await _seed_array_dataset(ctx, creator=ctx.request.user)
    crop = await seed.create_lens(ctx, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])
    elsewhere = await models.CoordinateSystem.objects.acreate(name="elsewhere", organization=ctx.request.organization)
    await models.Transformation.objects.acreate(kind="TRANSLATION", input_id=crop.coordinate_system_id, output=elsewhere, params={"translation": [0.0, 0.0]}, organization=ctx.request.organization)
    edges_before = await models.Transformation.objects.filter(input_id=crop.coordinate_system_id).acount()

    result = await schema.execute("mutation($id: ID!) { deleteLens(input: {id: $id}) }", variable_values={"id": str(crop.pk)}, context_value=ctx)
    assert not result.errors, result.errors

    assert not await models.Lens.objects.filter(pk=crop.pk).aexists()
    assert await models.CoordinateSystem.objects.filter(pk=crop.coordinate_system_id).aexists(), "the space stays: it is registered elsewhere"
    assert await models.Transformation.objects.filter(input_id=crop.coordinate_system_id).acount() == edges_before == 2, "with both its edges"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_container_takes_the_layers_drawn_through_its_lenses_in_every_scene(db, authenticated_context: HttpContext, bot_context: HttpContext):
    """A lens is refused while layers draw through it; its container is not. Deleting what a layer drew takes the layer, whoever's scene it is in, and the scene stays.

    Said rather than guarded: a scene has no owner to decide "someone else's" by, and the
    cascade is what a container's deletion has always meant. The lens' refusal names it as
    the one way to take every layer over a selection.
    """
    ctx = authenticated_context
    array_dataset = await _seed_array_dataset(ctx, creator=ctx.request.user)
    crop = await seed.create_lens(ctx, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])
    scene = await seed.create_scene(bot_context, "Bot's composition")
    layer = await models.Layer.objects.acreate(scene=scene, kind="image", lens=crop)

    refused = await schema.execute("mutation($id: ID!) { deleteLens(input: {id: $id}) }", variable_values={"id": str(crop.pk)}, context_value=ctx)
    assert refused.errors and "Deleting the container" in refused.errors[0].message, refused.errors

    result = await schema.execute("mutation($id: ID!) { deleteArrayDataset(input: {id: $id}) }", variable_values={"id": str(array_dataset.pk)}, context_value=ctx)
    assert not result.errors, result.errors
    assert not await models.Layer.objects.filter(pk=layer.pk).aexists(), "the layer went with what it drew"
    assert not await models.Lens.objects.filter(pk=crop.pk).aexists()
    assert await models.Scene.objects.filter(pk=scene.pk).aexists(), "the scene, which is not the dataset's, stays"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_datasets_whole_lens_cannot_be_deleted(db, authenticated_context: HttpContext):
    """The whole lens is the dataset's handle: it goes with the dataset, never on its own."""
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    whole = await seed.create_lens(authenticated_context, array_dataset)

    mutation = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    refused = await schema.execute(mutation, variable_values={"id": str(whole.pk)}, context_value=authenticated_context)

    assert refused.errors and "whole" in refused.errors[0].message, refused.errors
    assert await models.Lens.objects.filter(id=whole.pk).aexists()

    # With the dataset it goes.
    result = await schema.execute("mutation($id: ID!) { deleteArrayDataset(input: {id: $id}) }", variable_values={"id": str(array_dataset.pk)}, context_value=authenticated_context)
    assert not result.errors, result.errors
    assert not await models.Lens.objects.filter(id=whole.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_a_layer_draws_through_cannot_be_deleted(db, authenticated_context: HttpContext):
    """`Layer.lens` cascades and the lens is shared, so deleting it would empty scenes the caller never named."""
    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    lens = await seed.create_lens(authenticated_context, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])
    scene = await seed.create_scene(authenticated_context, "Composition")
    layer = await models.Layer.objects.acreate(scene=scene, kind="image", lens=lens)

    mutation = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    refused = await schema.execute(mutation, variable_values={"id": str(lens.pk)}, context_value=authenticated_context)

    assert refused.errors and "Composition" in refused.errors[0].message, refused.errors
    assert await models.Lens.objects.filter(id=lens.pk).aexists()

    await layer.adelete()
    allowed = await schema.execute(mutation, variable_values={"id": str(lens.pk)}, context_value=authenticated_context)
    assert not allowed.errors, allowed.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_a_chart_draws_through_cannot_be_deleted(db, authenticated_context: HttpContext):
    """The same guard, for the other composition that reads a lens."""
    from chart import models as chart_models

    array_dataset = await _seed_array_dataset(authenticated_context, creator=authenticated_context.request.user)
    lens = await seed.create_lens(authenticated_context, array_dataset, slices=[{"axis": "y", "start": 4, "stop": 20}])
    world = await models.CoordinateSystem.objects.acreate(name="Profile/world", organization=authenticated_context.request.organization)
    chart = await chart_models.Chart.objects.acreate(name="Profile", world=world, organization=authenticated_context.request.organization)
    await chart_models.ChartLayer.objects.acreate(chart=chart, kind="trace", lens=lens)

    mutation = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    refused = await schema.execute(mutation, variable_values={"id": str(lens.pk)}, context_value=authenticated_context)

    assert refused.errors and "chart 'Profile'" in refused.errors[0].message, refused.errors
    assert await models.Lens.objects.filter(id=lens.pk).aexists()


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
    image = await seed.create_array_dataset(authenticated_context, "Drawn")
    layer = await models.Layer.objects.acreate(scene=scene, kind="image", lens=await seed.create_lens(authenticated_context, image))

    mutation = "mutation($id: ID!) { deleteLayer(input: {id: $id}) }"
    result = await schema.execute(mutation, variable_values={"id": str(layer.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    assert not await models.Layer.objects.filter(id=layer.pk).aexists()
