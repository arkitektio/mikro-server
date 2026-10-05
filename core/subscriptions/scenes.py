from typing import AsyncGenerator

import strawberry
from kante.types import Info
from core import models, types, channels
from core.scoping import for_org
from ._relay import relay_rows


@strawberry.type(description="One change to the organization's scenes. Exactly one field is set per event")
class SceneEvent:
    create: types.Scene | None = strawberry.field(default=None, description="A scene that was created")
    update: types.Scene | None = strawberry.field(default=None, description="A scene that was edited, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of a scene that was deleted")


async def scenes(
    self: None,
    info: Info,
) -> AsyncGenerator[SceneEvent, None]:
    """Follow the organization's scenes: every one created, edited or deleted."""

    rooms = [channels.org_scenes_room(info.context.request.organization.id)]
    async for event in relay_rows(channels.scene_channel, info, rooms, for_org(models.Scene, info), SceneEvent):
        yield event
