from typing import AsyncGenerator

import strawberry
from django.core.exceptions import PermissionDenied
from kante.types import Info
from core import models, types, channels
from core.scoping import for_org
from ._relay import relay_rows


@strawberry.type(description="One change to a scene's layers. Exactly one field is set per event")
class LayerEvent:
    create: types.Layer | None = strawberry.field(default=None, description="A layer added to the scene")
    update: types.Layer | None = strawberry.field(default=None, description="A layer of the scene that was edited, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of a layer removed from the scene")


async def layers(
    self: None,
    info: Info,
    scene: strawberry.ID,
) -> AsyncGenerator[LayerEvent, None]:
    """Follow one scene: every layer added to it, edited or removed."""

    if not await for_org(models.Scene, info).filter(id=scene).aexists():
        raise PermissionDenied("Scene does not exist in this organization")

    scoped = for_org(models.Layer, info).filter(scene_id=scene)
    async for event in relay_rows(channels.layer_channel, info, [channels.scene_layers_room(scene)], scoped, LayerEvent):
        yield event
