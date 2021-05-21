from balder.types import BalderSubscription
from herre.bouncer.utils import bounced
import graphene
from grunnlag import models, types
import logging

logger = logging.getLogger(__name__)

class RepresentationEvent(graphene.ObjectType):
    ended =  graphene.ID()
    update =  graphene.Field(types.Representation)
    create = graphene.Field(types.Representation)

class MyRepresentations(BalderSubscription):
    USERGROUP = lambda user: f"representations_user_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = RepresentationEvent

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        logger.error(payload)

        if action == "updated":
            return {"update": models.Representation.objects.get(id=data)}
        if action == "created":
            return {"create": models.Representation.objects.get(id=data)}
        if action == "ended":
            return {"ended": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, **kwargs):
        return [MyRepresentations.USERGROUP(info.context.user)]


