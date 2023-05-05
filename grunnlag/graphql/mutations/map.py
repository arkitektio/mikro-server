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

class CreateDimensionMap(BalderMutation):
    """Creates a Feature
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        omero = graphene.ID(
            required=True, description="The stage this position belongs to"
        )
        dim = Dimension(description="The x coord of the position (relative to origin)", required=True)
        index = graphene.Int(description="The y coord of the position (relative to origin)", required=True)
        channel = graphene.ID(description="The channel you want to associate with this map", required=False)
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced(anonymous=False)
    def mutate(root, info, omero, dim, index, channel,  created_while=None):

        omero = models.Omero.objects.get(id=omero)
        channel = models.Channel.objects.get(id=channel) if channel else None

        map = models.DimensionMap.objects.create(
            omero=omero, dimension=dim, index=index, channel=channel, created_while=created_while, **fill_created(info)
        )
        
        return map

    class Meta:
        type = types.DimensionMap
        operation = "createDimensionMap"


class DeleteDimensionsMap(graphene.ObjectType):
    id = graphene.ID()


class DeleteDimensionMap(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the map to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.DimensionMap.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteDimensionsMap
