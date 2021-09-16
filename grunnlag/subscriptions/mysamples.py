from balder.types import BalderSubscription
from lok import bounced
import graphene
from grunnlag import models, types
import logging

logger = logging.getLogger(__name__)

class SamplesEvent(graphene.ObjectType):
    deleted =  graphene.ID()
    update =  graphene.Field(types.Sample)
    create = graphene.Field(types.Sample)

class MySamples(BalderSubscription):
    USERGROUP = lambda user: f"experiments_user_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = SamplesEvent

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        logger.error(payload)

        if action == "updated":
            return {"update": models.Sample.objects.get(id=data)}
        if action == "created":
            return {"create": models.Sample.objects.get(id=data)}
        if action == "deleted":
            return {"deleted": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, **kwargs):
        return [MySamples.USERGROUP(info.context.user)]


