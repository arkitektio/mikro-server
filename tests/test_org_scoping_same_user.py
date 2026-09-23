"""Cross-organization scoping for ONE user who belongs to two organizations.

``test_org_scoping`` pins the boundary with a different user in the other org. That misses
the case where the boundary actually gets crossed in practice: the same person, switching
the org they act in. Everything created while acting in ``static_org`` (token "test") must be
invisible, and unreferenceable, while acting in ``other_org`` (token "test-other-org").
"""

import pytest
from kante.context import HttpContext

from core.models import File, Folder, MetaSchema, UnstructuredMeta
from mikro_server.schema import schema
from tests.seed import create_array_dataset, create_file, create_folder, create_lens, create_scene, register_into_scene


#: Every top-level list field, by its SDL name. Each must come back empty for the other org.
LIST_FIELDS = [
    "folders",
    "myfolders",
    "files",
    "myfiles",
    "scenes",
    "layers",
    "lenses",
    "arrayDatasets",
    "dataArrays",
    "coordinateSystems",
    "transformations",
    "meshCollections",
    "networkCollections",
    "sparseDatasets",
    "tableDatasets",
    "tasks",
]


async def _seed(ctx: HttpContext) -> dict[str, str]:
    """Seed one of everything cheap in ``ctx``'s org; returns single-field name -> id."""
    folder = await create_folder(ctx, "Home")
    await folder.pinned_by.aadd(ctx.request.user)
    await create_file(ctx, "raw.tif", folder)
    dataset = await create_array_dataset(ctx, "Stack")
    lens = await create_lens(ctx, dataset)
    scene = await create_scene(ctx, "Scene")
    await register_into_scene(ctx, scene, dataset)
    data_array = await dataset.data_arrays.afirst()
    return {
        "folder": str(folder.pk),
        "arrayDataset": str(dataset.pk),
        "dataArray": str(data_array.pk),
        "coordinateSystem": str(dataset.coordinate_system_id),
        "lens": str(lens.pk),
        "scene": str(scene.pk),
    }


async def _ids(field: str, ctx: HttpContext, args: str = "") -> list[str]:
    result = await schema.execute(f"query {{ {field}{args} {{ id }} }}", context_value=ctx)
    assert not result.errors, result.errors
    return [row["id"] for row in result.data[field]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_list_fields_hide_the_other_org(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    await _seed(authenticated_context)

    # Sanity: the seeding org sees its own rows, so an empty list below means scoped, not broken.
    assert await _ids("folders", authenticated_context)
    assert await _ids("arrayDatasets", authenticated_context)

    leaks = {field: ids for field in LIST_FIELDS if (ids := await _ids(field, same_user_other_org_context))}
    assert not leaks, f"the same user acting in another org sees: {leaks}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_single_fields_refuse_the_other_org(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    seeded = await _seed(authenticated_context)

    leaked = []
    for field, id_ in seeded.items():
        query = f'query {{ {field}(id: "{id_}") {{ id }} }}'
        assert not (await schema.execute(query, context_value=authenticated_context)).errors, field
        result = await schema.execute(query, context_value=same_user_other_org_context)
        if not result.errors:
            leaked.append(field)
    assert not leaked, f"single fields readable from another org: {leaked}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pinned_and_mine_stay_in_the_acting_org(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    await _seed(authenticated_context)

    assert await _ids("folders", authenticated_context, "(filters: {pinned: true})")
    assert not await _ids("folders", same_user_other_org_context, "(filters: {pinned: true})")
    assert await _ids("myfolders", authenticated_context)
    assert not await _ids("myfolders", same_user_other_org_context)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_myfolders_only_lists_the_callers_folders(db, authenticated_context: HttpContext, bot_context: HttpContext):
    mine = await create_folder(authenticated_context, "Mine")
    theirs = await create_folder(bot_context, "Theirs")

    ids = await _ids("myfolders", authenticated_context)
    assert str(mine.pk) in ids
    assert str(theirs.pk) not in ids


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["createFolder", "ensureFolder", "updateFolder"])
async def test_folder_parent_must_be_in_the_acting_org(db, mutation: str, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    parent = await create_folder(authenticated_context, "Org A Parent")
    own = await create_folder(same_user_other_org_context, "Org B Folder")
    extra = f', id: "{own.pk}"' if mutation == "updateFolder" else ""

    result = await schema.execute(
        f"""
        mutation($parent: ID!) {{
            {mutation}(input: {{name: "Smuggled", parent: $parent{extra}}}) {{ id }}
        }}
        """,
        variable_values={"parent": str(parent.pk)},
        context_value=same_user_other_org_context,
    )
    assert result.errors, f"{mutation} nested a folder under another org's folder"
    assert not await Folder.objects.filter(name="Smuggled").aexists()
    assert not await Folder.objects.filter(parent=parent).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_rename_without_parent_keeps_the_parent(db, authenticated_context: HttpContext):
    parent = await create_folder(authenticated_context, "Parent")
    child = await create_folder(authenticated_context, "Child", parent=parent)

    result = await schema.execute(
        'mutation($id: ID!) { updateFolder(input: {id: $id, name: "Renamed"}) { id } }',
        variable_values={"id": str(child.pk)},
        context_value=authenticated_context,
    )
    assert not result.errors, result.errors
    await child.arefresh_from_db()
    assert child.name == "Renamed"
    assert child.parent_id == parent.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_children_do_not_cross_orgs(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    """Even a cross-org row already in the database (written before the fix) stays out of `children`."""
    parent = await create_folder(authenticated_context, "Org A Parent")
    await create_folder(same_user_other_org_context, "Org B Child", parent=parent)

    result = await schema.execute(
        'query($parent: ID!) { children(parent: $parent) { ... on Folder { id name } } }',
        variable_values={"parent": str(parent.pk)},
        context_value=authenticated_context,
    )
    assert not result.errors, result.errors
    assert result.data["children"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unstructured_meta_refuses_foreign_file_and_schema(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    folder = await create_folder(authenticated_context, "Org A")
    file = await create_file(authenticated_context, "a.tif", folder)
    foreign_schema = await MetaSchema.objects.acreate(name="s", schema={}, organization=authenticated_context.request.organization)
    own_folder = await create_folder(same_user_other_org_context, "Org B")
    own_file = await create_file(same_user_other_org_context, "b.tif", own_folder)

    mutation = """
        mutation($file: ID!, $schema: ID) {
            attachUnstructuredMeta(input: {name: "m", meta: {}, file: $file, schema: $schema}) { id }
        }
    """
    on_foreign_file = await schema.execute(mutation, variable_values={"file": str(file.pk)}, context_value=same_user_other_org_context)
    assert on_foreign_file.errors, "metadata was attached to another org's file"
    with_foreign_schema = await schema.execute(
        mutation, variable_values={"file": str(own_file.pk), "schema": str(foreign_schema.pk)}, context_value=same_user_other_org_context
    )
    assert with_foreign_schema.errors, "metadata referenced another org's schema"
    assert not await UnstructuredMeta.objects.aexists()
    assert await File.objects.acount() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_permissions_query_refuses_foreign_object(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    folder = await create_folder(authenticated_context, "Org A")

    result = await schema.execute(
        'query($id: ID!) { permissions(identifier: "@mikro/folder", object: $id) { permission } }',
        variable_values={"id": str(folder.pk)},
        context_value=same_user_other_org_context,
    )
    assert result.errors, "another org's object permissions were readable"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_migration_unnests_existing_cross_org_folders(db, authenticated_context: HttpContext, same_user_other_org_context: HttpContext):
    """The test settings never run migrations, so the data fix is exercised directly."""
    import importlib

    from asgiref.sync import sync_to_async
    from django.apps import apps

    migration = importlib.import_module("core.migrations.0014_unnest_cross_org_folders")
    parent = await create_folder(authenticated_context, "Org A Parent")
    same_org = await create_folder(authenticated_context, "Org A Child", parent=parent)
    cross_org = await create_folder(same_user_other_org_context, "Org B Child", parent=parent)

    a_schema = await MetaSchema.objects.acreate(name="a", schema={}, organization=authenticated_context.request.organization)
    a_file = await create_file(authenticated_context, "a.tif", parent)
    b_file = await create_file(same_user_other_org_context, "b.tif", cross_org)
    own_meta = await UnstructuredMeta.objects.acreate(name="own", meta={}, file=a_file, schema=a_schema)
    foreign_meta = await UnstructuredMeta.objects.acreate(name="foreign", meta={}, file=b_file, schema=a_schema)

    await sync_to_async(migration.unnest_cross_org_folders)(apps, None)
    await sync_to_async(migration.detach_cross_org_meta_schemas)(apps, None)

    await cross_org.arefresh_from_db()
    await same_org.arefresh_from_db()
    assert cross_org.parent_id is None
    assert same_org.parent_id == parent.pk
    await own_meta.arefresh_from_db()
    await foreign_meta.arefresh_from_db()
    assert own_meta.schema_id == a_schema.pk
    assert foreign_meta.schema_id is None
