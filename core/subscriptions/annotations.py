from typing import AsyncGenerator

import strawberry
from django.core.exceptions import PermissionDenied
from kante.types import Info
from core import models, types, channels
from core.scoping import for_org


@strawberry.type(description="One change to an annotation collection. Exactly one field is set per event")
class AnnotationEvent:
    create: types.Annotation | None = strawberry.field(default=None, description="An annotation drawn into the collection")
    update: types.Annotation | None = strawberry.field(default=None, description="An annotation of the collection that was edited, in its new state")
    delete: strawberry.ID | None = strawberry.field(default=None, description="The ID of an annotation deleted from the collection")


async def annotations(
    self,
    info: Info,
    collection: strawberry.ID,
) -> AsyncGenerator[AnnotationEvent, None]:
    """Follow one annotation collection: every shape drawn into it, edited or deleted."""

    if not await for_org(models.AnnotationCollection, info).filter(id=collection).aexists():
        raise PermissionDenied("Annotation collection does not exist in this organization")
    rooms = [channels.collection_annotations_room(collection)]

    # Re-fetched through the org scope AND the collection: the room name alone is not
    # what makes an event this subscriber's to see. A row that is gone by the time its
    # event is read (drawn and deleted in one breath) is skipped; its delete follows.
    scoped = for_org(models.Annotation, info).filter(collection_id=collection)

    async for message in channels.annotation_channel.listen(info, rooms):
        if message.create:
            annotation = await scoped.filter(id=message.create).afirst()
            if annotation:
                yield AnnotationEvent(create=annotation)

        elif message.create_many:
            async for annotation in scoped.filter(id__in=message.create_many).order_by("id"):
                yield AnnotationEvent(create=annotation)

        elif message.update:
            annotation = await scoped.filter(id=message.update).afirst()
            if annotation:
                yield AnnotationEvent(update=annotation)

        elif message.delete:
            yield AnnotationEvent(delete=message.delete)
