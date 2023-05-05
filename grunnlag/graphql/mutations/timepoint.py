from typing import Any
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types
from grunnlag.scalars import FeatureValue

from grunnlag.scalars import AssignationID

class CreateTimepoint(BalderMutation):
    """Creates a Timepoint
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        era = graphene.ID(
            required=True, description="The stage this position belongs to"
        )
        delta_t = graphene.Float(description="The x coord of the position (relative to origin)", required=True)
        name = graphene.String(required=False, description="The name of the position")
        tolerance = graphene.Float(description="The tolerance offset before we create a new timepoint", required=False)
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )
        created_while = AssignationID(required=False, description="The assignation id")


    @bounced(anonymous=False)
    def mutate(root, info, era, delta_t , name=None, tags=None,  created_while=None, tolerance=None):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        era = models.Era.objects.get(id=era)



        if not tolerance:
            timepoint, _ = models.Timepoint.objects.update_or_create(
                era=era, delta_t=delta_t, 
                defaults=dict(name=name or f"Timepoint {delta_t}", tags=tags or [], 
                created_while=created_while,)
            )
        else:
            timepoint = models.Timepoint.objects.filter(
                era=era, delta_t__gte=delta_t-tolerance, delta_t__lte=delta_t+tolerance,
            ).first()
            if not timepoint:
                timepoint = models.Timepoint.objects.create(
                    created_while=created_while,
                    era=era, delta_t=delta_t, name=name or f"Timepoint {delta_t}", tags=tags or []
                )

        
        return timepoint

    class Meta:
        type = types.Timepoint
        operation = "createTimepoint"


class DeleteTimepointResult(graphene.ObjectType):
    id = graphene.String()


class DeleteTimepoint(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Timepoint.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteTimepointResult


class PinTimepoint(BalderMutation):
    """Pin Acquisition
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Timepoint.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Timepoint