from balder.types import BalderSubscription
from herre.bouncer.utils import bounced
import graphene
from grunnlag import models, types
import logging

logger = logging.getLogger(__name__)

class ExperimentsEvent(graphene.ObjectType):
    deleted =  graphene.ID()
    update =  graphene.Field(types.Experiment)
    create = graphene.Field(types.Experiment)

class MyExperiments(BalderSubscription):
    USERGROUP = lambda user: f"experiments_user_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = ExperimentsEvent

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        logger.error(payload)

        if action == "updated":
            return {"update": models.Experiment.objects.get(id=data)}
        if action == "created":
            return {"create": models.Experiment.objects.get(id=data)}
        if action == "deleted":
            return {"deleted": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, **kwargs):
        return [MyExperiments.USERGROUP(info.context.user)]


