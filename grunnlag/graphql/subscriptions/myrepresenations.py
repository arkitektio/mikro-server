from balder.types import BalderSubscription
from lok import bounced
import graphene
from grunnlag import models, types
import logging

logger = logging.getLogger(__name__)


class RepresentationEvent(graphene.ObjectType):
    deleted = graphene.ID()
    update = graphene.Field(types.Representation)
    create = graphene.Field(types.Representation)


class MyRepresentations(BalderSubscription):
    USERGROUP = lambda user: f"representations_user_{user.id}"
    USER_NO_CHILDRENGROUP = lambda user: f"representations_user_{user.id}_no_children"
    ORIGIN_GROUP = lambda origin: f"representations_origin_{origin.id}"

    class Arguments:
        origin = graphene.ID(
            required=False, description="Only get updates for a certain origin"
        )
        stream_children = graphene.Boolean(
            required=False, description="Stream children"
        )

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
        if action == "deleted":
            return {"deleted": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, origin=None, stream_children=False):
        if origin is not None:
            origin = models.Representation.objects.get(id=origin)
            return [MyRepresentations.ORIGIN_GROUP(origin)]

        if stream_children:
            return [MyRepresentations.USERGROUP(info.context.user)]

        return [MyRepresentations.USER_NO_CHILDRENGROUP(info.context.user)]
