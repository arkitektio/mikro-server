from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from lok import bounced
from grunnlag.utils import fill_created

from grunnlag.scalars import AssignationID
class CreateExperiment(BalderMutation):
    """Create an Experiment
    
    This mutation creates an Experiment and returns the created Experiment.
    """

    class Arguments:
        name = graphene.String(
            required=True,
            description="A name for the experiment",
        )
        description = graphene.String(
            required=False, description="A short description of the experiment"
        )
        creator = graphene.String(
            required=False,
            description="The user that created this experiment (defaults to the logined user)",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(
        root, info, name=None, description=None, creator=None, created_while=None, tags=[]
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Experiment.objects.create(
            creator=creator, description=description, created_while=created_while, name=name, **fill_created(info)
        )
        if tags:
            exp.tags.add(*tags)
        return exp

    class Meta:
        type = types.Experiment


class DeleteExperimentResult(graphene.ObjectType):
    id = graphene.String()


class DeleteExperiment(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Experiment.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteExperimentResult


class UpdateExperiment(BalderMutation):
    """ Update an Experiment
    
    This mutation updates an Experiment and returns the updated Experiment."""

    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String(
            required=True,
            description="The name of the experiment",
        )
        description = graphene.String(
            required=False, description="A short description of the experiment"
        )
        creator = graphene.String(
            required=False,
            description="The user that created this experiment (defaults to the logined user)",
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

        exp = models.Experiment.objects.get(id=id)

        if description:
            exp.description = description
        if name:
            exp.name = name
        if tags:
            exp.tags.add(*tags)

        exp.save()
        return exp

    class Meta:
        type = types.Experiment


class PinExperiment(BalderMutation):
    """Pin Experiment
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Experiment.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Experiment



class AssociateSamples(BalderMutation):


    class Arguments:
        samples = graphene.List(graphene.ID, required=True)
        experiment = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, samples, experiment, **kwargs):
        experiment = models.Experiment.objects.get(id=experiment)
        samples = models.Sample.objects.filter(id__in=samples)
        experiment.samples.add(*samples)
        return experiment

    class Meta:
        type = types.Experiment

class UnassociateSamples(BalderMutation):


    class Arguments:
        samples = graphene.List(graphene.ID, required=True)
        experiment = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, samples, experiment, **kwargs):
        experiment = models.Experiment.objects.get(id=experiment)
        samples = models.Sample.objects.filter(id__in=samples)
        experiment.samples.remove(*samples)
        experiment.save()
        return experiment

    class Meta:
        type = types.Experiment
        

class AssociateFiles(BalderMutation):


    class Arguments:
        files = graphene.List(graphene.ID, required=True)
        experiment = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, files, experiment, **kwargs):
        experiment = models.Experiment.objects.get(id=experiment)
        files = models.OmeroFile.objects.filter(id__in=files)
        experiment.omero_files.add(*files)
        experiment.save()
        return experiment

    class Meta:
        type = types.Experiment
        

class UnassociateFiles(BalderMutation):


    class Arguments:
        files = graphene.List(graphene.ID, required=True)
        experiment = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, files, experiment, **kwargs):
        experiment = models.Experiment.objects.get(id=experiment)
        files = models.OmeroFile.objects.filter(id__in=files)
        experiment.omero_files.remove(*files)
        experiment.save()
        return experiment

    class Meta:
        type = types.Experiment