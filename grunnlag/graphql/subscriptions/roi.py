import graphene
from grunnlag import types, models
from lok import bounced
from balder.types import BalderSubscription


class RoiEvent(graphene.ObjectType):
    delete = graphene.ID()
    update = graphene.Field(types.ROI)
    create = graphene.Field(types.ROI)


class Rois(BalderSubscription):
    ROI_FOR_REP = lambda rep: f"rois_for_rep_{rep.id}"
    ROI_FOR_REPID = lambda id: f"rois_for_rep_{id}"

    class Arguments:
        representation = graphene.ID(
            description="The representation to filter on", required=True
        )
        pass

    class Meta:
        type = RoiEvent
        operation = "rois"

    def publish(payload, info, *args, **kwargs):
        payload = payload["payload"]
        action = payload["action"]
        data = payload["data"]

        if action == "updated":
            return {"update": data}
        if action == "created":
            return {"create": data}
        if action == "deleted":
            return {"delete": data}

    @bounced(only_jwt=True)
    def subscribe(root, info, *args, representation=None, **kwargs):
        return [Rois.ROI_FOR_REPID(representation)]
