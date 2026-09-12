"""Filter tests for the files and tables queries (FileFilter, TableFilter)."""

import pytest

from core.enums import FileLinkDirectionChoices
from core.models import ArrayDataset, FileLink
from kante.context import HttpContext
from mikro_server.schema import schema

from tests.seed import create_folder, create_file

FILE_QUERY = """
    query List($filters: FileFilter) {
        files(filters: $filters) { id name }
    }
"""

TABLE_QUERY = """
    query List($filters: TableFilter) {
        tables(filters: $filters) { id name }
    }
"""


async def file_names(ctx, filters):
    result = await schema.execute(FILE_QUERY, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {f["name"] for f in result.data["files"]}


async def table_names(ctx, filters):
    result = await schema.execute(TABLE_QUERY, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {t["name"] for t in result.data["tables"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_file_filter_by_folder(db, authenticated_context: HttpContext):
    ds_a = await create_folder(authenticated_context, "A")
    ds_b = await create_folder(authenticated_context, "B")
    await create_file(authenticated_context, "InA", ds_a)
    await create_file(authenticated_context, "InB", ds_b)

    assert await file_names(authenticated_context, {"folder": str(ds_a.id)}) == {"InA"}
    assert await file_names(authenticated_context, {"folders": [str(ds_a.id), str(ds_b.id)]}) == {"InA", "InB"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_file_filter_by_size_and_content_type(db, authenticated_context: HttpContext):
    ds = await create_folder(authenticated_context, "DS")
    await create_file(authenticated_context, "Small", ds, size=100, content_type="text/csv")
    await create_file(authenticated_context, "Big", ds, size=10_000, content_type="image/tiff")

    assert await file_names(authenticated_context, {"size": {"gte": 1000}}) == {"Big"}
    assert await file_names(authenticated_context, {"contentType": {"exact": "text/csv"}}) == {"Small"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_file_filter_by_not_derived(db, authenticated_context: HttpContext):
    """`notDerived` separates the files a converter read from the files written out of data here.

    It used to read the `File.origins` M2M, which no resolver ever wrote -- so it answered
    `true` for every file and `false` for none, and this test passed by only ever adding to
    that M2M by hand. It now reads the file's links, which the mutations actually write.
    """
    ctx = authenticated_context
    ds = await create_folder(ctx, "DS")
    await create_file(ctx, "Original", ds)
    exported = await create_file(ctx, "Derived", ds)

    dataset = await ArrayDataset.objects.acreate(name="Source data", creator=ctx.request.user, organization=ctx.request.organization)
    await FileLink.objects.acreate(
        file=exported,
        dataset=dataset,
        direction=FileLinkDirectionChoices.RENDITION.value,
        creator=ctx.request.user,
        organization=ctx.request.organization,
    )

    assert await file_names(ctx, {"notDerived": True}) == {"Original"}
    assert await file_names(ctx, {"notDerived": False}) == {"Derived"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_file_filter_by_search(db, authenticated_context: HttpContext):
    ds = await create_folder(authenticated_context, "DS")
    await create_file(authenticated_context, "measurements.csv", ds)
    await create_file(authenticated_context, "raw.tiff", ds)

    assert await file_names(authenticated_context, {"search": "MEASURE"}) == {"measurements.csv"}
