from balder.types import BalderSubscription
from lok import bounced
import graphene
from komment import models, types
import logging

logger = logging.getLogger(__name__)


class MentionEvent(graphene.ObjectType):
    deleted = graphene.ID()
    update = graphene.Field(types.Comment)
    create = graphene.Field(types.Comment)


class MyMentionsSubscription(BalderSubscription):
    """My Mentions

    Returns an event of a new mention for the user if the user 
    was mentioned in a comment.
    """

    USERGROUP = lambda user: f"mymentions_{user.id}"

    class Arguments:
        pass

    class Meta:
        type = MentionEvent
        operation = "mymentions"

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        logger.error(payload)

        if action == "updated":
            return {"update": models.Comment.objects.get(id=data)}
        if action == "created":
            return {"create": models.Comment.objects.get(id=data)}
        if action == "deleted":
            return {"deleted": data}

        logger.error("error in payload")

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, **kwargs):
        return [MyMentionsSubscription.USERGROUP(info.context.user)]
