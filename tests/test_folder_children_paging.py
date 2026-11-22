"""`children` pages a folder's contents as one list, and `childrenCounts` counts that list.

The kinds are separate tables. A page is chosen from all of them at once, so an order is
an order across kinds (a file named "a" before a folder named "b"), a kind filter narrows
before paging (a page of tables is not an empty page because folders came first), and the
counts are of the same filtered list.
"""

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_fileable_folder import _create_array_dataset, _create_table

CHILDREN = """
query Children($parent: ID!, $filters: FolderChildrenFilter, $order: ChildrenOrder, $pagination: ChildrenPaginationInput) {
  children(parent: $parent, filters: $filters, order: $order, pagination: $pagination) {
    __typename
    ... on Folder { name }
    ... on File { name }
    ... on ArrayDataset { name }
    ... on TableDataset { name }
    ... on SparseDataset { name }
    ... on NetworkCollection { label }
  }
}
"""

COUNTS = """
query Counts($parent: ID!, $filters: FolderChildrenFilter) {
  childrenCounts(parent: $parent, filters: $filters) { total kinds { kind count } }
}
"""


async def _children(ctx: HttpContext, folder: models.Folder, **variables: Any) -> list[dict[str, Any]]:
    result = await schema.execute(CHILDREN, context_value=ctx, variable_values={"parent": str(folder.pk), **variables})
    assert not result.errors, result.errors
    assert result.data
    return result.data["children"]


def _name(child: dict[str, Any]) -> str:
    return child.get("name") or child.get("label") or ""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_order_by_name_interleaves_the_kinds(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Mixed")
    await seed.create_folder(ctx, "charlie", parent=folder)
    await seed.create_file(ctx, "alpha.txt", folder)
    await _create_array_dataset(ctx, "bravo", folder=folder)
    await _create_table(ctx, "delta", folder=folder)

    ascending = await _children(ctx, folder, order={"field": "NAME", "direction": "ASC"})
    assert [_name(c) for c in ascending] == ["alpha.txt", "bravo", "charlie", "delta"]

    descending = await _children(ctx, folder, order={"field": "NAME", "direction": "DESC"})
    assert [_name(c) for c in descending] == ["delta", "charlie", "bravo", "alpha.txt"]

    second_page = await _children(ctx, folder, order={"field": "NAME", "direction": "ASC"}, pagination={"limit": 2, "offset": 2})
    assert [_name(c) for c in second_page] == ["charlie", "delta"], "a page is a slice of the one ordered list"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_size_orders_files_and_datasets_together_with_the_unsized_last(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Sized")
    await seed.create_file(ctx, "big.bin", folder, size=5000)
    await seed.create_file(ctx, "small.bin", folder, size=10)
    await seed.create_folder(ctx, "unsized", parent=folder)

    largest_first = await _children(ctx, folder, order={"field": "SIZE", "direction": "DESC"})
    assert [_name(c) for c in largest_first] == ["big.bin", "small.bin", "unsized"]

    smallest_first = await _children(ctx, folder, order={"field": "SIZE", "direction": "ASC"})
    assert [_name(c) for c in smallest_first] == ["small.bin", "big.bin", "unsized"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_kind_filter_pages_through_that_kind_only(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Tables among folders")
    for index in range(3):
        await seed.create_folder(ctx, f"sub {index}", parent=folder)
    await _create_table(ctx, "t1", folder=folder)
    await _create_table(ctx, "t2", folder=folder)

    tables = await _children(ctx, folder, filters={"kinds": ["TABLE_DATASET"]}, pagination={"limit": 1, "offset": 1})
    assert [c["__typename"] for c in tables] == ["TableDataset"], "the first page of tables is a table, not a sub-folder"

    counts = await schema.execute(COUNTS, context_value=ctx, variable_values={"parent": str(folder.pk), "filters": {"kinds": ["TABLE_DATASET", "FOLDER"]}})
    assert not counts.errors, counts.errors
    assert counts.data
    answer = counts.data["childrenCounts"]
    assert answer["total"] == 5
    assert {entry["kind"]: entry["count"] for entry in answer["kinds"]} == {"FOLDER": 3, "TABLE_DATASET": 2}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_counts_cover_every_kind_and_follow_the_search(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Counted")
    await seed.create_file(ctx, "notes.txt", folder)
    await _create_array_dataset(ctx, "Kidney", folder=folder)
    await _create_array_dataset(ctx, "Liver", folder=folder)

    everything = await schema.execute(COUNTS, context_value=ctx, variable_values={"parent": str(folder.pk)})
    assert not everything.errors, everything.errors
    assert everything.data
    by_kind = {entry["kind"]: entry["count"] for entry in everything.data["childrenCounts"]["kinds"]}
    assert everything.data["childrenCounts"]["total"] == 3
    assert by_kind["ARRAY_DATASET"] == 2 and by_kind["FILE"] == 1 and by_kind["FOLDER"] == 0
    assert len(by_kind) == 9, "every kind is answered, zeros included"

    searched = await schema.execute(COUNTS, context_value=ctx, variable_values={"parent": str(folder.pk), "filters": {"search": "Kidney"}})
    assert not searched.errors, searched.errors
    assert searched.data
    assert searched.data["childrenCounts"]["total"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sparse_datasets_and_network_collections_are_listed(authenticated_context: HttpContext):
    ctx = authenticated_context
    folder = await seed.create_folder(ctx, "Newer containers")
    store = await seed.create_konnektion_store(ctx, axes=["x", "y", "z"])
    created = await schema.execute(
        "mutation Create($input: CreateNetworkCollectionInput!) { createNetworkCollection(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "name": "arbor",
                "store": str(store.pk),
                "folder": str(folder.pk),
                "axes": [{"name": axis, "type": "SPACE"} for axis in ("x", "y", "z")],
            }
        },
    )
    assert not created.errors, created.errors

    def file_a_matrix() -> None:
        models.SparseDataset.objects.create(name="expression", folder=folder, organization=ctx.request.organization, creator=ctx.request.user)

    await sync_to_async(file_a_matrix)()

    listed = await _children(ctx, folder, order={"field": "NAME", "direction": "ASC"})
    assert [(c["__typename"], _name(c)) for c in listed] == [("NetworkCollection", "arbor"), ("SparseDataset", "expression")]
