from typing import AsyncGenerator

import strawberry
from django.core.exceptions import PermissionDenied
from kante.types import Info
from core import models, types, channels
from core.scoping import for_org
from ._relay import relay_rows


@strawberry.type(description="One change to the array datasets being followed. Exactly one field is set per event")
class ArrayDatasetEvent:
    create: types.ArrayDataset | None = strawberry.field(default=None, description="An array dataset that was created")
    update: types.ArrayDataset | None = strawberry.field(default=None, description="An array dataset that was edited, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of an array dataset that was deleted")


@strawberry.type(description="One change to the table datasets being followed. Exactly one field is set per event")
class TableDatasetEvent:
    create: types.TableDataset | None = strawberry.field(default=None, description="A table dataset that was created")
    update: types.TableDataset | None = strawberry.field(default=None, description="A table dataset that was edited, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of a table dataset that was deleted")


async def _assert_folder(info: Info, folder: strawberry.ID) -> None:
    if not await for_org(models.Folder, info).filter(id=folder).aexists():
        raise PermissionDenied("Folder does not exist in this organization")


async def array_datasets(
    self: None,
    info: Info,
    folder: strawberry.ID | None = None,
) -> AsyncGenerator[ArrayDatasetEvent, None]:
    """Follow the array datasets of one folder, or of the whole organization when none is given."""

    scoped = for_org(models.ArrayDataset, info)
    if folder is None:
        rooms = [channels.org_array_datasets_room(info.context.request.organization.id)]
    else:
        await _assert_folder(info, folder)
        rooms = [channels.folder_array_datasets_room(folder)]
        scoped = scoped.filter(folder_id=folder)

    async for event in relay_rows(channels.array_dataset_channel, info, rooms, scoped, ArrayDatasetEvent):
        yield event


async def table_datasets(
    self: None,
    info: Info,
    folder: strawberry.ID | None = None,
) -> AsyncGenerator[TableDatasetEvent, None]:
    """Follow the table datasets of one folder, or of the whole organization when none is given."""

    scoped = for_org(models.TableDataset, info)
    if folder is None:
        rooms = [channels.org_table_datasets_room(info.context.request.organization.id)]
    else:
        await _assert_folder(info, folder)
        rooms = [channels.folder_table_datasets_room(folder)]
        scoped = scoped.filter(folder_id=folder)

    async for event in relay_rows(channels.table_dataset_channel, info, rooms, scoped, TableDatasetEvent):
        yield event
