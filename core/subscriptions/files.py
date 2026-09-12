from typing import AsyncGenerator

import strawberry
from django.core.exceptions import PermissionDenied
from kante.types import Info
from core import models, types, channels
from core.scoping import aget_for_org, for_org


@strawberry.type
class FileEvent:
    create: types.File | None = None
    delete: strawberry.ID | None = None
    update: types.File | None = None
    moved: types.File | None = None


async def files(
    self,
    info: Info,
    folder: strawberry.ID | None = None,
) -> AsyncGenerator[FileEvent, None]:
    """Join and subscribe to message sent to the given rooms."""

    if folder is None:
        rooms = [channels.org_files_room(info.context.request.organization.id)]
    else:
        if not await for_org(models.Folder, info).filter(id=folder).aexists():
            raise PermissionDenied("Folder does not exist in this organization")
        rooms = [channels.folder_files_room(folder)]

    async for message in channels.file_channel.listen(info.context, rooms):
        if message.create:
            file = await aget_for_org(models.File, info, id=message.create)
            yield FileEvent(create=file)

        elif message.delete:
            yield FileEvent(delete=message.delete)

        elif message.update:
            file = await aget_for_org(models.File, info, id=message.update)
            yield FileEvent(update=file)
