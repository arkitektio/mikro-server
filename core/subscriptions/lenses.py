from typing import AsyncGenerator

import strawberry
from django.core.exceptions import PermissionDenied
from kante.types import Info

from core import channels, enums, models, types
from core.scoping import for_org

from ._relay import relay_rows


@strawberry.type(description="One change to the lenses being followed. Exactly one field is set per event")
class LensEvent:
    create: types.Lens | None = strawberry.field(default=None, description="A lens that was made: a crop, a window, or the whole lens a new container was born with")
    update: types.Lens | None = strawberry.field(default=None, description="A lens that was renamed or nominated a scene, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of a lens that was deleted, on its own or with its container")


async def lenses(
    self: None,
    info: Info,
    kind: enums.LensKind | None = None,
    container: strawberry.ID | None = None,
) -> AsyncGenerator[LensEvent, None]:
    """Follow the lenses of one container (its kind and id), or of the whole organization when none is given."""
    if (kind is None) != (container is None):
        raise ValueError("A container is followed by its kind and its id together: give both, or neither for the organization's lenses")

    if kind is None or container is None:
        rooms = [channels.org_lenses_room(info.context.request.organization.id)]
        scoped = for_org(models.Lens, info)
    else:
        lens_model = next(model for model in models.Lens.KIND_BY_CONTAINER.values() if model.KIND == kind.value)
        container_model = next(model for model, lens_kind in models.Lens.KIND_BY_CONTAINER.items() if lens_kind is lens_model)
        if not await for_org(container_model, info).filter(id=container).aexists():
            raise PermissionDenied(f"{container_model.__name__} does not exist in this organization")
        rooms = [channels.container_lenses_room(kind.value, container)]
        scoped = for_org(lens_model, info).filter(**{f"{lens_model.CONTAINER_FIELD}_id": container})

    async for event in relay_rows(channels.lens_channel, info, rooms, scoped.with_sources(), LensEvent):
        yield event
