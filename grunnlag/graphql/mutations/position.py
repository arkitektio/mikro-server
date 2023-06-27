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

class CreatePosition(BalderMutation):
    """Creates a Feature
    
    This mutation creates a Feature and returns the created Feature.
    We require a reference to the label that the feature belongs to.
    As well as the key and value of the feature.
    
    There can be multiple features with the same label, but only one feature per key
    per label"""

    class Arguments:
        stage = graphene.ID(
            required=True, description="The stage this position belongs to"
        )
        name = graphene.String(required=False, description="The name of the position")
        x = graphene.Float(description="The x coord of the position (relative to origin)", required=True)
        y = graphene.Float(description="The y coord of the position (relative to origin)", required=True)
        z = graphene.Float(description="The z coord of the position (relative to origin)", required=True)
        roi_origins = graphene.List(
            graphene.ID,
            required=False,
            description="The Rois that were used to define this position",
        )
        creator = graphene.ID(description="The creator of this position")
        tolerance = graphene.Float(description="The tolerance offset before we create a new position", required=False)
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )
        created_while = AssignationID(required=False, description="The assignation id")


    @bounced(anonymous=False)
    def mutate(root, info, stage, x, y, z, creator=None, name=None, tags=None,  created_while=None, tolerance=None, roi_origins=None, **kwargs):
        creator = info.context.user or (
            get_user_model().objects.get(id=creator) if creator else None
        )
        assert creator is not None, "Creator is required if using a backend app"

        stage = models.Stage.objects.get(id=stage)



        if not tolerance:
            position, _ = models.Position.objects.update_or_create(
                stage=stage, x=x, y=y, z=z,
                defaults=dict(name=name or f"Position {x} {y} {z} ", tags=tags or [], created_while=created_while)
                
            )
        else:
            position = models.Position.objects.filter(
                stage=stage, x__gte=x-tolerance, x__lte=x+tolerance,
                y__gte=y-tolerance, y__lte=y+tolerance,
                z__gte=z-tolerance, z__lte=z+tolerance,
            ).first()
            if not position:
                position = models.Position.objects.create(
                    created_while=created_while,
                    stage=stage, x=x, y=y, z=z, name=name or f"Position {x} {y} {z} ", tags=tags or []
                )

        if roi_origins:
            position.roi_origins.set(models.ROI.objects.filter(id__in=roi_origins))
            position.save()


        
        return position

    class Meta:
        type = types.Position
        operation = "createPosition"


class DeletePositionResult(graphene.ObjectType):
    id = graphene.String()


class DeletePosition(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Position.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeletePositionResult


class PinPosition(BalderMutation):
    """Pin Acquisition
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Position.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Position


class AddPosition(BalderMutation):
    """Add Posistion
    
    This mutation adds a position to an experiment and returns the experiment."""

    class Arguments:
        omero = graphene.ID(required=True, description="The ID of the omero")
        position = graphene.ID(required=True, description="The ID of the position to add to the representation")

    @bounced()
    def mutate(root, info, omero, position, view=None, **kwargs):
        omero = models.Omero.objects.get(id=omero)

        if view:
            view = models.View.objects.get(id=view)

        position = models.Position.objects.get(id=position)

        omero.positions.add(position)
        omero.save()


        return omero

    class Meta:
        type = types.Omero


class RemovePosition(BalderMutation):
    """Remove Posistion
    
    This mutation adds a position to an experiment and returns the experiment."""

    class Arguments:
        omero = graphene.ID(required=True, description="The ID of the omero")
        position = graphene.ID(required=True, description="The ID of the position to remove from representation")

    @bounced()
    def mutate(root, info, omero, position, view=None, **kwargs):
        omero = models.Omero.objects.get(id=omero)

        if view:
            view = models.View.objects.get(id=view)

        position = models.Position.objects.get(id=position)

        omero.positions.remove(position)
        omero.save()


        return omero

    class Meta:
        type = types.Omero