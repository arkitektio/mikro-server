"""Filter tests for the folders query (FolderFilter)."""

import pytest
from asgiref.sync import sync_to_async

from kante.context import HttpContext
from mikro_server.schema import schema

from tests.seed import create_folder, create_other_user

QUERY = """
    query List($filters: FolderFilter) {
        folders(filters: $filters) { id name }
    }
"""


async def names(ctx, filters):
    result = await schema.execute(QUERY, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {ds["name"] for ds in result.data["folders"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_parentless(db, authenticated_context: HttpContext):
    root = await create_folder(authenticated_context, "Root")
    await create_folder(authenticated_context, "Child", parent=root)

    assert await names(authenticated_context, {"parentless": True}) == {"Root"}
    assert await names(authenticated_context, {"parentless": False}) == {"Child"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_is_default(db, authenticated_context: HttpContext):
    await create_folder(authenticated_context, "Default", is_default=True)
    await create_folder(authenticated_context, "Regular")

    assert await names(authenticated_context, {"isDefault": True}) == {"Default"}
    assert await names(authenticated_context, {"isDefault": False}) == {"Regular"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_tags_and_pinned(db, authenticated_context: HttpContext):
    tagged = await create_folder(authenticated_context, "Tagged")
    await create_folder(authenticated_context, "Plain")
    await sync_to_async(tagged.tags.add)("screening")
    await sync_to_async(tagged.pinned_by.add)(authenticated_context.request.user)

    assert await names(authenticated_context, {"tags": ["screening"]}) == {"Tagged"}
    assert await names(authenticated_context, {"pinned": True}) == {"Tagged"}
    assert await names(authenticated_context, {"pinned": False}) == {"Plain"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_owner(db, authenticated_context: HttpContext):
    other = await create_other_user(authenticated_context)
    from authentikate.models import Membership

    other_membership = await Membership.objects.aget(
        user=other, organization=authenticated_context.request.organization
    )
    await create_folder(authenticated_context, "Mine")
    await create_folder(authenticated_context, "Theirs", creator=other, membership=other_membership)

    assert await names(authenticated_context, {"owner": "2"}) == {"Theirs"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_search_and_description(db, authenticated_context: HttpContext):
    await create_folder(authenticated_context, "Experiment", description="control wells")
    await create_folder(authenticated_context, "Other", description="treated wells")

    assert await names(authenticated_context, {"search": "Experiment"}) == {"Experiment"}
    assert await names(authenticated_context, {"description": {"iContains": "control"}}) == {"Experiment"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_parent(db, authenticated_context: HttpContext):
    """The parent filter lists the direct children of a folder."""
    root = await create_folder(authenticated_context, "Root")
    await create_folder(authenticated_context, "Child A", parent=root)
    await create_folder(authenticated_context, "Child B", parent=root)
    await create_folder(authenticated_context, "Unrelated")

    assert await names(authenticated_context, {"parent": str(root.id)}) == {"Child A", "Child B"}
