from balder.types import BalderSubscription
from lok import bounced
import graphene
from bord import models
from grunnlag import types
import logging

logger = logging.getLogger(__name__)


class TablesEvent(graphene.ObjectType):
    deleted = graphene.ID()
    update = graphene.Field(types.Table)
    create = graphene.Field(types.Table)


class MyTables(BalderSubscription):
    USERGROUP = lambda user: f"tables_user_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = TablesEvent

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        logger.error(payload)

        if action == "updated":
            return {"update": models.Table.objects.get(id=data)}
        if action == "created":
            return {"create": models.Table.objects.get(id=data)}
        if action == "deleted":
            return {"deleted": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, **kwargs):
        return [MyTables.USERGROUP(info.context.user)]
