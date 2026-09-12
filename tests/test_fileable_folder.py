"""Everything fileable can be filed: the four containers carry a folder.

``File`` has always had one. ``ArrayDataset``, ``TableDataset``,
``MeshCollection`` and ``AnnotationCollection`` -- the same four ``FileLink`` calls "a
container holding data" -- now do too, so the folder tree is a complete view of a user's
data rather than a view of the older half of it.

The invariant these tests defend is that filing and *placement* stay separate. A folder
says where a user keeps a thing; it never says anything about the space the thing is in.
Nothing here touches a coordinate system, and nothing in the coordinate-graph tests should
ever need to touch a folder.
"""

from typing import Any
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from datalayer.models import ZarrStore
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE_ADATASET = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id folder { id name } }
}
"""

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id folder { id name } }
}
"""

CREATE_MESH = """
mutation Create($input: CreateMeshCollectionInput!) {
  createMeshCollection(input: $input) { id folder { id name } }
}
"""

CREATE_ANNOTATION_COLLECTION = """
mutation Create($input: CreateAnnotationCollectionInput!) {
  createAnnotationCollection(input: $input) { id folder { id name } }
}
"""

_YX = [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]


async def _zarr(ctx: HttpContext, key: str) -> ZarrStore:
    return await ZarrStore.objects.acreate(
        organization=ctx.request.organization,
        key=key,
        bucket="zarr",
        shape=[32, 32],
        chunks=[32, 32],
        version="3",
        dtype="uint8",
        populated=True,
    )


async def _parquet(ctx: HttpContext, key: str, columns: list[tuple[str, str]] | None = None) -> models.ParquetStore:
    """A finished store carrying the file's own schema.

    The schema is load-bearing since 3b: `createTableDataset` reads a column's name and type
    off the store rather than from the caller, so a store that records none has nothing for a
    table to be declared over -- and `_resolve_store` refuses it rather than reaching for an S3
    no unit test has.
    """
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization,
        populated=True, columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in (columns or [])],
    )


async def _create_array_dataset(ctx: HttpContext, name: str, folder=None, derived_from=None) -> dict[str, Any]:
    store = await _zarr(ctx, f"zarr-{name}")
    payload = {"name": name, "data": str(store.pk), "scales": [], "axes": _YX}
    if folder is not None:
        payload["folder"] = str(folder.pk)
    if derived_from is not None:
        payload["derivedFrom"] = derived_from
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(CREATE_ADATASET, context_value=ctx, variable_values={"input": payload})
    assert not result.errors, result.errors
    assert result.data
    return result.data["createArrayDataset"]


async def _create_table(ctx: HttpContext, name: str, folder=None, derived_from=None) -> dict[str, Any]:
    store = await _parquet(ctx, f"table-{name}", [("object", "BIGINT")])
    payload = {
        "name": name,
        "data": str(store.pk),
        "columns": [{"name": "object", "dtype": "BIGINT", "axisType": "INDEX"}],
    }
    if folder is not None:
        payload["folder"] = str(folder.pk)
    if derived_from is not None:
        payload["derivedFrom"] = derived_from
    result = await schema.execute(CREATE_TABLE, context_value=ctx, variable_values={"input": payload})
    assert not result.errors, result.errors
    assert result.data
    return result.data["createTableDataset"]


async def _create_mesh(ctx: HttpContext, version: str, folder=None, derived_from=None) -> dict[str, Any]:
    store = await seed.create_fabriks_store(ctx)
    payload = {"axes": _YX, "version": version, "store": str(store.pk)}
    if folder is not None:
        payload["folder"] = str(folder.pk)
    if derived_from is not None:
        payload["derivedFrom"] = derived_from
    result = await schema.execute(CREATE_MESH, context_value=ctx, variable_values={"input": payload})
    assert not result.errors, result.errors
    assert result.data
    return result.data["createMeshCollection"]


async def _create_annotation_collection(ctx: HttpContext, name: str, folder=None, derived_from=None) -> dict[str, Any]:
    payload = {"name": name, "axes": _YX}
    if folder is not None:
        payload["folder"] = str(folder.pk)
    if derived_from is not None:
        payload["derivedFrom"] = derived_from
    result = await schema.execute(CREATE_ANNOTATION_COLLECTION, context_value=ctx, variable_values={"input": payload})
    assert not result.errors, result.errors
    assert result.data
    return result.data["createAnnotationCollection"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_container_files_into_a_named_folder(authenticated_context: HttpContext):
    """All four containers accept a folder on create and read it back."""
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Experiment A")

    created = [
        await _create_array_dataset(ctx, "Acquired", folder=folder),
        await _create_table(ctx, "Measurements", folder=folder),
        await _create_mesh(ctx, "v1", folder=folder),
        await _create_annotation_collection(ctx, "Drawn", folder=folder),
    ]

    for container in created:
        assert container["folder"] is not None, "a container created with a folder must report it"
        assert container["folder"]["name"] == "Experiment A"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_container_created_without_a_folder_lands_in_the_default(authenticated_context: HttpContext):
    """Omitting the folder files it in the user's default, exactly as an image has always been.

    The column is nullable and unfiled rows are legal -- migration 0007 does not backfill --
    but nothing created *through the API* is left unfiled.
    """
    dataset = await _create_array_dataset(authenticated_context, "Unfiled")

    assert dataset["folder"] is not None, "a container created without a folder still gets the default one"
    assert dataset["folder"]["name"] == "Default"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_folder_lists_every_kind_of_container_it_holds(authenticated_context: HttpContext):
    """The reverse lists on Folder, one per container type."""
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Everything")

    await _create_array_dataset(ctx, "Acquired", folder=folder)
    await _create_table(ctx, "Measurements", folder=folder)
    await _create_mesh(ctx, "v1", folder=folder)
    await _create_annotation_collection(ctx, "Drawn", folder=folder)

    result = await schema.execute(
        """
        query Contents($id: ID!) {
          folder(id: $id) {
            arrayDatasets { name }
            tableDatasets { name }
            meshCollections { version }
            annotationCollections { name }
          }
        }
        """,
        context_value=ctx,
        variable_values={"id": str(folder.pk)},
    )
    assert not result.errors, result.errors
    assert result.data

    contents = result.data["folder"]
    assert [d["name"] for d in contents["arrayDatasets"]] == ["Acquired"]
    assert [t["name"] for t in contents["tableDatasets"]] == ["Measurements"]
    assert [m["version"] for m in contents["meshCollections"]] == ["v1"]
    assert [a["name"] for a in contents["annotationCollections"]] == ["Drawn"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_children_returns_the_containers_alongside_files(authenticated_context: HttpContext):
    """`children` is the folder's contents, so it has to include what folders can now hold.

    It returned only sub-folders and files while the containers were unfileable;
    leaving it that way would have made a folder's contents list quietly incomplete.
    """
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Mixed")
    await seed.create_file(ctx, "raw.czi", folder)
    await _create_array_dataset(ctx, "Acquired", folder=folder)
    await _create_table(ctx, "Measurements", folder=folder)
    await _create_mesh(ctx, "v1", folder=folder)
    await _create_annotation_collection(ctx, "Drawn", folder=folder)

    result = await schema.execute(
        """
        query Children($parent: ID!) {
          children(parent: $parent) { __typename }
        }
        """,
        context_value=ctx,
        variable_values={"parent": str(folder.pk)},
    )
    assert not result.errors, result.errors
    assert result.data

    kinds = {child["__typename"] for child in result.data["children"]}
    assert kinds == {"File", "ArrayDataset", "TableDataset", "MeshCollection", "AnnotationCollection"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_children_orders_and_searches_across_every_source(authenticated_context: HttpContext):
    """The `order` and `search` branches, which fan across all seven sources.

    Worth its own test because `children` builds its querysets from a table now, and both
    branches ask each source a question it has to be able to answer. `MeshCollection` is the
    one that cannot answer `name` -- it has no such column, it is identified by `version` --
    so it is ordered and searched by that instead. Without this test the rewrite was only
    ever exercised on the branch where neither runs.
    """
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Sortable")
    await _create_array_dataset(ctx, "Beta", folder=folder)
    await _create_table(ctx, "Alpha", folder=folder)
    await _create_mesh(ctx, "v9", folder=folder)
    await _create_annotation_collection(ctx, "Gamma", folder=folder)

    ordered = await schema.execute(
        """
        query Children($parent: ID!, $order: ChildrenOrder) {
          children(parent: $parent, order: $order) { __typename }
        }
        """,
        context_value=ctx,
        variable_values={"parent": str(folder.pk), "order": {"field": "NAME", "direction": "ASC"}},
    )
    assert not ordered.errors, ordered.errors
    assert ordered.data
    assert len(ordered.data["children"]) == 4, "ordering by name must not drop the source that has no name column"

    by_created = await schema.execute(
        """
        query Children($parent: ID!, $order: ChildrenOrder) {
          children(parent: $parent, order: $order) { __typename }
        }
        """,
        context_value=ctx,
        variable_values={"parent": str(folder.pk), "order": {"field": "CREATED_AT", "direction": "DESC"}},
    )
    assert not by_created.errors, by_created.errors
    assert by_created.data
    assert len(by_created.data["children"]) == 4

    searched = await schema.execute(
        """
        query Children($parent: ID!, $filters: FolderChildrenFilter) {
          children(parent: $parent, filters: $filters) { __typename }
        }
        """,
        context_value=ctx,
        variable_values={"parent": str(folder.pk), "filters": {"search": "Alpha"}},
    )
    assert not searched.errors, searched.errors
    assert searched.data
    assert [c["__typename"] for c in searched.data["children"]] == ["TableDataset"], "search must reach the containers, and only match one here"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_containers_are_filterable_by_folder(authenticated_context: HttpContext):
    """`folder` and `folders` on each container filter, and they filter independently."""
    ctx = authenticated_context
    here = await seed.create_folder(ctx, "Here")
    there = await seed.create_folder(ctx, "There")

    await _create_array_dataset(ctx, "Mine", folder=here)
    await _create_array_dataset(ctx, "Theirs", folder=there)
    await _create_table(ctx, "MyTable", folder=here)
    await _create_table(ctx, "TheirTable", folder=there)

    result = await schema.execute(
        """
        query ByFolder($here: ID!, $both: [ID!]) {
          here: arrayDatasets(filters: {folder: $here}) { name }
          both: arrayDatasets(filters: {folders: $both}) { name }
          tables: tableDatasets(filters: {folder: $here}) { name }
        }
        """,
        context_value=ctx,
        variable_values={"here": str(here.pk), "both": [str(here.pk), str(there.pk)]},
    )
    assert not result.errors, result.errors
    assert result.data

    assert [d["name"] for d in result.data["here"]] == ["Mine"]
    assert {d["name"] for d in result.data["both"]} == {"Mine", "Theirs"}
    assert [t["name"] for t in result.data["tables"]] == ["MyTable"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_folder_unfiles_its_contents_and_destroys_nothing(authenticated_context: HttpContext):
    """`on_delete=SET_NULL` everywhere: a folder is organisational, so deleting one says
    nothing about whether its contents should exist.

    It was CASCADE, which destroyed data through the database relation and so bypassed the
    per-object delete guards (`self_owner` on `deleteArrayDataset`) entirely. Deleting the data
    itself stays where it belongs, on the delete mutation for the thing.

    All four, plus the older holders: the failure this guards against is not "does the
    unfiling happen" but "does a PROTECT FK pointing at a container turn folder deletion
    into a ProtectedError". `AnnotationCollection` is the one to watch, since it holds a
    PROTECT reference to its coordinate system.
    """
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Doomed")

    file = await seed.create_file(ctx, "raw.czi", folder)
    dataset = await _create_array_dataset(ctx, "Survives", folder=folder)
    table = await _create_table(ctx, "AlsoSurvives", folder=folder)
    mesh = await _create_mesh(ctx, "v1", folder=folder)
    collection = await _create_annotation_collection(ctx, "AndThis", folder=folder)

    await sync_to_async(models.Folder.objects.filter(pk=folder.pk).delete)()

    async def survives_unfiled(model, pk) -> bool:
        """Still there, and no longer filed. Asked as a query so `<fk>_id` is never read."""
        return await model.objects.filter(pk=pk, folder__isnull=True).aexists()

    assert await survives_unfiled(models.File, file.pk)
    assert await survives_unfiled(models.ArrayDataset, dataset["id"])
    assert await survives_unfiled(models.TableDataset, table["id"])
    assert await survives_unfiled(models.MeshCollection, mesh["id"])
    assert await survives_unfiled(models.AnnotationCollection, collection["id"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_container_can_be_refiled_and_unfiled(authenticated_context: HttpContext):
    """`put<Things>InFolder` / `release<Things>FromFolder` for all four containers.

    Without these a container could be filed once, at creation, and never moved: `folder`
    was on the create inputs and on nothing else. Releasing unfiles and deletes nothing.
    """
    ctx = authenticated_context
    origin = await seed.create_folder(ctx, "Origin")
    destination = await seed.create_folder(ctx, "Destination")

    dataset = await _create_array_dataset(ctx, "Moves", folder=origin)
    table = await _create_table(ctx, "AlsoMoves", folder=origin)
    mesh = await _create_mesh(ctx, "v1", folder=origin)
    collection = await _create_annotation_collection(ctx, "AndThis", folder=origin)

    cases = [
        ("putArrayDatasetsInFolder", "releaseArrayDatasetsFromFolder", models.ArrayDataset, dataset["id"]),
        ("putTableDatasetsInFolder", "releaseTableDatasetsFromFolder", models.TableDataset, table["id"]),
        ("putMeshCollectionsInFolder", "releaseMeshCollectionsFromFolder", models.MeshCollection, mesh["id"]),
        ("putAnnotationCollectionsInFolder", "releaseAnnotationCollectionsFromFolder", models.AnnotationCollection, collection["id"]),
    ]

    for put, release, model, pk in cases:
        moved = await schema.execute(
            f"mutation M($input: AssociateInput!) {{ {put}(input: $input) {{ id name }} }}",
            context_value=ctx,
            variable_values={"input": {"selfs": [str(pk)], "other": str(destination.pk)}},
        )
        assert not moved.errors, moved.errors
        assert await model.objects.filter(pk=pk, folder=destination).aexists(), f"{put} must re-file it"

        freed = await schema.execute(
            f"mutation M($input: DesociateInput!) {{ {release}(input: $input) {{ id }} }}",
            context_value=ctx,
            variable_values={"input": {"selfs": [str(pk)], "other": str(destination.pk)}},
        )
        assert not freed.errors, freed.errors
        assert await model.objects.filter(pk=pk, folder__isnull=True).aexists(), f"{release} must unfile it"
        assert await model.objects.filter(pk=pk).aexists(), f"{release} must not delete it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_releasing_a_file_from_a_folder_no_longer_violates_not_null(authenticated_context: HttpContext):
    """`File.folder` was NOT NULL while `releaseFilesFromFolder` set it to None.

    Every call was an IntegrityError. Making the column nullable -- which SET_NULL needed
    anyway -- is what that mutation always assumed.
    """
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Holding")
    file = await seed.create_file(ctx, "raw.czi", folder)

    result = await schema.execute(
        "mutation M($input: DesociateInput!) { releaseFilesFromFolder(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"selfs": [str(file.pk)], "other": str(folder.pk)}},
    )
    assert not result.errors, result.errors

    assert await models.File.objects.filter(pk=file.pk, folder__isnull=True).aexists()



@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_derived_data_is_filed_with_its_parent_and_cannot_be_filed_alone(authenticated_context: HttpContext):
    """Only root data is filed explicitly; a derivation inherits and refuses a folder of its own.

    Kind-blind: the table below derives UNMAPPABLY from the dataset -- its rows are
    per-object measurements and are not anywhere -- and it is still filed with it. Filing
    is a historical question, not a spatial one.
    """
    ctx = authenticated_context
    home = await seed.create_folder(ctx, "Home")
    elsewhere = await seed.create_folder(ctx, "Elsewhere")

    parent = await _create_array_dataset(ctx, "Acquired", folder=home)

    derived = await _create_table(ctx, "Measurements", derived_from=[{"kind": "DATASET", "dataset": parent["id"]}])
    assert derived["folder"]["name"] == "Home", "a derivation is filed where its parent is"

    refused = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={
            "input": {
                "name": "Rejected",
                "data": str((await _parquet(ctx, "rejected", [("object", "BIGINT")])).pk),
                "columns": [{"name": "object", "dtype": "BIGINT", "axisType": "INDEX"}],
                "derivedFrom": [{"kind": "DATASET", "dataset": parent["id"]}],
                "folder": str(elsewhere.pk),
            }
        },
    )
    assert refused.errors, "naming a folder for derived data must be refused, not silently ignored"
    assert "filed with it" in str(refused.errors[0])

    moved = await schema.execute(
        "mutation M($input: AssociateInput!) { putTableDatasetsInFolder(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"selfs": [str(derived["id"])], "other": str(elsewhere.pk)}},
    )
    assert moved.errors, "a derived container cannot be re-filed on its own either"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_moving_a_parent_moves_everything_derived_from_it(authenticated_context: HttpContext):
    """The stored copy stays honest: re-filing a root rewrites its descendants, transitively."""
    ctx = authenticated_context
    home = await seed.create_folder(ctx, "Home")
    destination = await seed.create_folder(ctx, "Destination")

    root = await _create_array_dataset(ctx, "Acquired", folder=home)
    child = await _create_table(ctx, "Measurements", derived_from=[{"kind": "DATASET", "dataset": root["id"]}])
    grandchild = await _create_mesh(ctx, "v1", derived_from=[{"kind": "TABLE_DATASET", "tableDataset": child["id"]}])

    assert await models.MeshCollection.objects.filter(pk=grandchild["id"], folder=home).aexists(), "inheritance is transitive at creation"

    moved = await schema.execute(
        "mutation M($input: AssociateInput!) { putArrayDatasetsInFolder(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"selfs": [str(root["id"])], "other": str(destination.pk)}},
    )
    assert not moved.errors, moved.errors

    assert await models.ArrayDataset.objects.filter(pk=root["id"], folder=destination).aexists()
    assert await models.TableDataset.objects.filter(pk=child["id"], folder=destination).aexists(), "the child follows"
    assert await models.MeshCollection.objects.filter(pk=grandchild["id"], folder=destination).aexists(), "and so does the grandchild"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_secondary_parent_does_not_carry_the_filing(authenticated_context: HttpContext):
    """A fusion sits with its *primary* parent, matching the rule placement already uses."""
    ctx = authenticated_context
    first_home = await seed.create_folder(ctx, "First")
    second_home = await seed.create_folder(ctx, "Second")
    destination = await seed.create_folder(ctx, "Destination")

    primary = await _create_array_dataset(ctx, "Primary", folder=first_home)
    secondary = await _create_array_dataset(ctx, "Secondary", folder=second_home)

    fusion = await _create_table(
        ctx,
        "Fused",
        derived_from=[
            {"kind": "DATASET", "dataset": primary["id"]},
            {"kind": "DATASET", "dataset": secondary["id"]},
        ],
    )
    assert fusion["folder"]["name"] == "First", "the first declared source is the primary parent"

    moved = await schema.execute(
        "mutation M($input: AssociateInput!) { putArrayDatasetsInFolder(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"selfs": [str(secondary["id"])], "other": str(destination.pk)}},
    )
    assert not moved.errors, moved.errors
    assert await models.TableDataset.objects.filter(pk=fusion["id"], folder=first_home).aexists(), "moving a secondary parent must not drag the fusion along"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filing_says_nothing_about_placement(authenticated_context: HttpContext):
    """Two datasets in one folder share no space, and one dataset's folder is not its system.

    The point of the whole feature: `folder` is organisational and the coordinate graph is
    geometric. If these two ever start informing each other, this test is the one that
    should fail first.
    """
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "One Folder")

    first = await _create_array_dataset(ctx, "First", folder=folder)
    second = await _create_array_dataset(ctx, "Second", folder=folder)

    result = await schema.execute(
        """
        query Systems($a: ID!, $b: ID!) {
          a: arrayDataset(id: $a) { folder { id } intrinsicSystem { id } }
          b: arrayDataset(id: $b) { folder { id } intrinsicSystem { id } }
        }
        """,
        context_value=ctx,
        variable_values={"a": first["id"], "b": second["id"]},
    )
    assert not result.errors, result.errors
    assert result.data

    a, b = result.data["a"], result.data["b"]
    assert a["folder"]["id"] == b["folder"]["id"], "same folder"
    assert a["intrinsicSystem"]["id"] != b["intrinsicSystem"]["id"], "sharing a folder must not mean sharing a space"
