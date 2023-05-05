from typing import Any
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput, Dimension
from grunnlag import models, types
from grunnlag.scalars import FeatureValue
from grunnlag.utils import fill_created
from grunnlag.scalars import AssignationID
from grunnlag.inputs import ViewInput

class CreateView(BalderMutation):
    """Creates a Feature
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        view = ViewInput(required=True, description="The view to create")

    @bounced(anonymous=False)
    def mutate(root, info, view):

        channel = view.pop("channel")

        omero = models.Omero.objects.get(id=omero)
        channel = models.Channel.objects.get(id=channel) if channel else None

        map = models.View.objects.create(
             channel= channel, **fill_created(info), **view
        )
        
        return map

    class Meta:
        type = types.View
        operation = "createView"


class DeleteViewReturn(graphene.ObjectType):
    id = graphene.ID()


class DeleteView(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the map to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.View.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteViewReturn
