from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types

from grunnlag.scalars import AssignationID

class InputVector(graphene.InputObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")
    c = graphene.Float(description="C-coordinate")
    t = graphene.Float(description="T-coordinate")


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
        label = graphene.String(required=False, description="The label of the ROI")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the roi?",
        )
        type = graphene.Argument(
            RoiTypeInput, description="The type of ROI", required=True
        )
        meta = GenericScalar(required=False, description="Meta Parameters")
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced(anonymous=False)
    def mutate(
        root,
        info,
        representation=None,
        vectors=[],
        creator=None,
        meta=None,
        label=None,
         created_while=None,
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
            creator=creator, vectors=vectors, created_while=created_while, representation=rep, type=type, label=label
        )

        if tags:
            roi.tags.add(*tags)

        return roi

    class Meta:
        type = types.ROI


class CreateROIS(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        representation = graphene.ID(
            required=True, description="The Representation this ROI belongs to"
        )
        vectors_list = graphene.List(graphene.List(
            InputVector,
            required=True,
            description="The Experiments this sample Belongs to",
        ), description="the List of Vectors")
        creator = graphene.ID(
            required=False, description="The email of the creator, only for backend app"
        )
        labels = graphene.List(graphene.String, required=False, description="The label of the ROI")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the roi?",
        )
        type = graphene.Argument(
            RoiTypeInput, description="The type of ROI", required=True
        )
        created_while = AssignationID(required=False, description="The assignation id")
        meta = GenericScalar(required=False, description="Meta Parameters")

    @bounced(anonymous=False)
    def mutate(
        root,
        info,
        representation=None,
        vectors_list=[],
        creator=None,
        meta=None,
        labels=None,
        type=None,
        tags=[],
    ):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        rep = models.Representation.objects.get(id=representation)

        if labels:
            assert len(labels) == len(vectors_list), "If provided Labels and Vectors must have the same length"

        for i, vectors in enumerate(vectors_list):
            roi = models.ROI.objects.create(
                creator=creator, vectors=vectors, representation=rep, type=type, label=labels[i] if labels else None
            )

            if tags:
                roi.tags.add(*tags)


        return rep

    class Meta:
        type = types.Representation



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


class PinROI(BalderMutation):
    """Sets the pin"""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.ROI.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.ROI
