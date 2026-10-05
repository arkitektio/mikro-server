from collections.abc import AsyncGenerator, Callable

import strawberry
from django.db.models import Model, QuerySet
from kante.channel import Channel
from kante.types import Info

from core.channel_signals import RowSignal


async def relay_rows[M: Model, E](
    channel: Channel[RowSignal],
    info: Info,
    rooms: list[str],
    scoped: QuerySet[M],
    event: Callable[..., E],
) -> AsyncGenerator[E, None]:
    """Turn the ids relayed to ``rooms`` into events, re-fetching each row through ``scoped``.

    ``scoped`` is the subscriber's own view (its organization, and the parent it follows):
    the room name alone is not what makes an event this subscriber's to see. A row that is
    gone by the time its event is read (created and deleted in one breath) is skipped; its
    delete follows.

    ``event`` is the event type itself, built with exactly one of ``create``, ``update`` or
    ``delete``.
    """
    async for message in channel.listen(info, rooms):
        if message.create is not None:
            row = await scoped.filter(pk=message.create).afirst()
            if row:
                yield event(create=row)

        elif message.update is not None:
            row = await scoped.filter(pk=message.update).afirst()
            if row:
                yield event(update=row)

        elif message.delete is not None:
            yield event(delete=strawberry.ID(str(message.delete)))
