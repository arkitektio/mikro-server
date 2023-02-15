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





class CreateStage(BalderMutation):
    """Creates a Stage
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        name = graphene.String(required=False, description="The name of the position")
        instrument = graphene.ID(
            required=False, description="The acquisition this position belongs to"
        )
        creator = graphene.ID(description="The creator of this position")
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )


    @bounced(anonymous=False)
    def mutate(root, info, instrument=None, kind=None, creator=None, name=None , tags=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        acqui = models.Stage.objects.create(creator=creator, instrument_id=instrument, kind=kind or "UNKNOWN", name=name, tags=tags or [], **fill_created(info))


        return acqui

    class Meta:
        type = types.Stage
        operation = "createStage"

class UpdateStage(BalderMutation):
    """ Update an Experiment
    
    This mutation updates an Experiment and returns the updated Experiment."""

    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String(
            required=True,
            description="The name of the experiment",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )

    @bounced()
    def mutate(
        root, info, id, name=None, description=None, meta=None, creator=None, tags=[]
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Stage.objects.get(id=id)

        if name:
            exp.name = name
        if tags:
            exp.tags.add(*tags)

        exp.save()
        return exp

    class Meta:
        type = types.Stage



class DeleteStageResult(graphene.ObjectType):
    id = graphene.String()


class DeleteStage(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Stage.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteStageResult


class PinStage(BalderMutation):
    """Pin Stage
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Stage.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Stage