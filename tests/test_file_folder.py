"""``File.folder``: where a file is filed, null once its folder is gone."""

import pytest
from kante.context import HttpContext

from core.models import Folder
from mikro_server.schema import schema
from tests.seed import create_file, create_folder

QUERY = """
    query ($filters: FileFilter) {
        files(filters: $filters) { name folder { id name } }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_file_exposes_its_folder(db, authenticated_context: HttpContext) -> None:
    folder = await create_folder(authenticated_context, "Raw data")
    await create_file(authenticated_context, "plate.czi", folder)

    result = await schema.execute(QUERY, context_value=authenticated_context, variable_values={"filters": {}})
    assert not result.errors, result.errors
    assert result.data["files"] == [{"name": "plate.czi", "folder": {"id": str(folder.pk), "name": "Raw data"}}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_folder_unfiles_the_file(db, authenticated_context: HttpContext) -> None:
    folder = await create_folder(authenticated_context, "Raw data")
    await create_file(authenticated_context, "plate.czi", folder)
    await Folder.objects.filter(pk=folder.pk).adelete()

    result = await schema.execute(QUERY, context_value=authenticated_context, variable_values={"filters": {}})
    assert not result.errors, result.errors
    assert result.data["files"] == [{"name": "plate.czi", "folder": None}]
