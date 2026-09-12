"""Cross-organization scoping: one org must not see or mutate another org's rows.

Single-object queries, mutations and subscriptions all go through
core.scoping (see that module); these tests pin the behaviour with a user
from a second organization (the "othertest" static token).
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from kante.context import HttpContext

from core.models import Folder
from core import subscriptions
from mikro_server.schema import schema
from tests.seed import create_folder


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_folder_is_org_scoped(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    folder = await create_folder(authenticated_context, "Org A Folder")

    mutation = """
        mutation($id: ID!) {
            deleteFolder(input: {id: $id})
        }
    """

    denied = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=other_org_context)
    assert denied.errors, "a user from another organization could delete the folder"
    assert await Folder.objects.filter(id=folder.pk).aexists()

    allowed = await schema.execute(mutation, variable_values={"id": str(folder.pk)}, context_value=authenticated_context)
    assert not allowed.errors, allowed.errors
    assert not await Folder.objects.filter(id=folder.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pin_folder_toggles_and_is_org_scoped(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    folder = await create_folder(authenticated_context, "Org A Folder")

    mutation = """
        mutation($id: ID!, $pin: Boolean!) {
            pinFolder(input: {id: $id, pin: $pin}) { id }
        }
    """

    pinned = await schema.execute(mutation, variable_values={"id": str(folder.id), "pin": True}, context_value=authenticated_context)
    assert not pinned.errors, pinned.errors
    assert await folder.pinned_by.acount() == 1

    unpinned = await schema.execute(mutation, variable_values={"id": str(folder.id), "pin": False}, context_value=authenticated_context)
    assert not unpinned.errors, unpinned.errors
    assert await folder.pinned_by.acount() == 0

    denied = await schema.execute(mutation, variable_values={"id": str(folder.id), "pin": True}, context_value=other_org_context)
    assert denied.errors, "a user from another organization could pin the folder"
