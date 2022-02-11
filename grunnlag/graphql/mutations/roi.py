from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types


class InputVector(graphene.InputObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")


class CreateROI(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        representation = graphene.ID(
            required=True, description="The Representation this ROI belongs to"
        )
        vectors = graphene.List(
            InputVector,
            required=True,
            description="The Experiments this sample Belongs to",
        )
        creator = graphene.ID(
            required=False, description="The email of the creator, only for backend app"
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the roi?",
        )
        type = graphene.Argument(RoiTypeInput, description="The type of ROI")
        meta = GenericScalar(required=False, description="Meta Parameters")

    @bounced(anonymous=False)
    def mutate(
        root,
        info,
        representation=None,
        vectors=[],
        creator=None,
        meta=None,
        type=None,
        tags=[],
    ):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        rep = models.Representation.objects.get(id=representation)

        print(type)
        roi = models.ROI.objects.create(
            creator=creator, vectors=vectors, representation=rep, type=type
        )

        if tags:
            roi.tags.add(*tags)

        return roi

    class Meta:
        type = types.ROI


class DeleteROIResult(graphene.ObjectType):
    id = graphene.String()


class DeleteROI(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="A cleartext description what this representation represents as data",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        sample = models.ROI.objects.get(id=id)
        sample.delete()
        return {"id": id}

    class Meta:
        type = DeleteROIResult
