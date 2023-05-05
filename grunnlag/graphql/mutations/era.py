from typing import Any
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput, AcquisitionKind
from grunnlag import models, types
from grunnlag.scalars import FeatureValue
from grunnlag.utils import fill_created




from grunnlag.scalars import AssignationID

class CreateEra(BalderMutation):
    """Creates a Stage
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        name = graphene.String(required=False, description="The name of the era")
        start = graphene.DateTime(required=False, description="The start of this era")
        end = graphene.DateTime(required=False, description="The end of this era")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )
        created_while = AssignationID(required=False, description="The assignation id")


    @bounced(anonymous=False)
    def mutate(root, info, creator=None, name=None ,  created_while=None, start=None, end=None, tags=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        acqui = models.Era.objects.create(created_while=created_while, start=start, end=end, name=name, tags=tags or [], **fill_created(info))


        return acqui

    class Meta:
        type = types.Era
        operation = "createEra"



class DeleteEraResult(graphene.ObjectType):
    id = graphene.String()


class DeleteEra(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Era.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteEraResult


class PinEra(BalderMutation):
    """Pin Stage
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Era.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Era