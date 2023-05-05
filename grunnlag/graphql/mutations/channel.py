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

class CreateChannel(BalderMutation):
    """Creates a Feature
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        name = graphene.String(description="The name of the channel")
        emission_wavelength = graphene.Float(description="The emmission wavelength of the fluorophore in nm")
        excitation_wavelength = graphene.Float(description="The excitation wavelength of the fluorophore in nm")
        acquisition_mode = graphene.String(description="The acquisition mode of the channel")
        color = graphene.String(description="The default color for the channel (might be ommited by the rendered)")

    @bounced(anonymous=False)
    def mutate(root, info, name =None, emission_wavelength=None, excitation_wavelength=None, acquisition_mode=None, color=None,  created_while=None):

        channel, _  = models.Channel.objects.update_or_create(
            name = name,
            emission_wavelength = emission_wavelength,
            excitation_wavelength = excitation_wavelength,
            defaults=dict(acquisition_mode=acquisition_mode, color=color,created_while=created_while, **fill_created(info))
        )





        
        return channel

    class Meta:
        type = types.Channel
        operation = "createChannel"


class DeleteChannelResult(graphene.ObjectType):
    id = graphene.ID()


class DeleteChannel(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the map to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Channel.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteChannelResult
