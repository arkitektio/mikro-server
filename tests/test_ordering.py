"""Ordering tests for the new order_type classes (ordering: [XOrder!] argument)."""

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests.seed import create_file, create_folder


async def execute(ctx: HttpContext, query: str, ordering: list[dict]) -> dict:
    result = await schema.execute(
        query, context_value=ctx, variable_values={"ordering": ordering}
    )
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_files_order_by_size_then_name(
    db: None, authenticated_context: HttpContext
) -> None:
    """Multiple ordering keys apply in list order."""
    ctx = authenticated_context
    ds = await create_folder(ctx, "DS")
    await create_file(ctx, "b.bin", ds, size=100)
    await create_file(ctx, "a.bin", ds, size=100)
    await create_file(ctx, "big.bin", ds, size=900)

    query = """
        query List($ordering: [FileOrder!]!) {
            files(ordering: $ordering) { name }
        }
    """
    data = await execute(ctx, query, [{"size": "DESC"}, {"name": "ASC"}])
    assert [f["name"] for f in data["files"]] == ["big.bin", "a.bin", "b.bin"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_folders_order_by_name(
    db: None, authenticated_context: HttpContext
) -> None:
    ctx = authenticated_context
    for name in ["Zeta", "Alpha", "Mid"]:
        await create_folder(ctx, name)

    query = """
        query List($ordering: [FolderOrder!]!) {
            folders(ordering: $ordering) { name }
        }
    """
    data = await execute(ctx, query, [{"name": "DESC"}])
    assert [d["name"] for d in data["folders"]] == ["Zeta", "Mid", "Alpha"]
