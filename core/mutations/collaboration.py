"""Putting several objects in a collaboration in one go: a dataset with what was derived from it.

The single ``addToCollaboration`` is ``arkitekt_service``'s; this batch is mikro's, over the same
``collaboration_items.add``, so every object still brings what it needs to be seen.
"""

from typing import Any

import strawberry
from arkitekt_service.collaborations import graphql as collaboration_types
from arkitekt_service.collaborations import items as collaboration_items
from arkitekt_service.collaborations import models as collaboration_models
from django.db import transaction
from kante.types import Info

from core.scoping import get_for_org


@strawberry.input(description="One object of a batch, by the structure it is known by and its id")
class CollaborationObjectRef:
    identifier: str = strawberry.field(description="The structure the object is known by: @mikro/arraydataset")
    object: strawberry.ID = strawberry.field(description="The object's id")


@strawberry.input(description="Objects to put in a collaboration together")
class AddManyToCollaborationInput:
    collaboration: strawberry.ID
    objects: list[CollaborationObjectRef] = strawberry.field(
        description="What to put in, in order: a whole before its parts. Each brings what it needs to be seen; one that is already in stays as it is"
    )


def _object(info: Info, ref: CollaborationObjectRef) -> Any:
    from arkitekt_service.service.service import Service

    structure = Service.hosting(ref.identifier)
    if structure is None:
        raise ValueError(f"No structure {ref.identifier!r} is hosted here")
    return get_for_org(structure.model, info, id=ref.object)


def add_many_to_collaboration(info: Info, input: AddManyToCollaborationInput) -> collaboration_types.Collaboration:
    """Put several objects in a collaboration, all or none."""
    target = collaboration_models.Collaboration.objects.get(pk=input.collaboration, organization=info.context.request.organization)
    # Once each, first mention first: the order is what keeps a whole ahead of its parts.
    refs: dict[tuple[str, str], CollaborationObjectRef] = {}
    for ref in input.objects:
        refs.setdefault((ref.identifier, str(ref.object)), ref)
    with transaction.atomic():
        for ref in refs.values():
            collaboration_items.add(target, _object(info, ref), by=info.context.request.user)
    return target  # type: ignore[return-value]
