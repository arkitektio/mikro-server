from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from lok import bounced
from grunnlag.utils import fill_created

class CreateDataset(BalderMutation):
    """Create an Experiment
    
    This mutation creates an Experiment and returns the created Experiment.
    """

    class Arguments:
        name = graphene.String(
            required=True,
            description="A name for the experiment",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )
        parent = graphene.ID(
            required=False,
            description="The parent of this dataset",
        )

    @bounced()
    def mutate(
        root, info, name=None, parent=None, tags=[]
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Dataset.objects.create(
            name=name, parent=parent, **fill_created(info)
        )
        if tags:
            exp.tags.add(*tags)
        return exp

    class Meta:
        type = types.Dataset


class DeleteDatasetResult(graphene.ObjectType):
    id = graphene.String()


class DeleteDataset(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Dataset.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteDatasetResult


class UpdateDataset(BalderMutation):
    """ Update an Experiment
    
    This mutation updates an Experiment and returns the updated Experiment."""

    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String(
            required=True,
            description="The name of the experiment",
        )
        parent = graphene.ID(
            required=False,
            description="The parent experiment",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Tags for the experiment",
        )

    @bounced()
    def mutate(
        root, info, id, name=None, parent=None, tags=[]
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Dataset.objects.get(id=id)

        if parent:
            exp.parent_id = parent
        if name:
            exp.name = name
        if tags:
            exp.tags.add(*tags)

        exp.save()
        return exp

    class Meta:
        type = types.Dataset


class PinDataset(BalderMutation):
    """Pin Experiment
    
    This mutation pins an Experiment and returns the pinned Experiment."""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin state")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Dataset.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Dataset



class PutSamples(BalderMutation):


    class Arguments:
        samples = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, samples, dataset, **kwargs):
        dataset = models.Dataset.objects.get(id=dataset)
        samples = models.Sample.objects.filter(id__in=samples)
        dataset.samples.add(*samples)
        return dataset

    class Meta:
        type = types.Dataset

class ReleaseSamples(BalderMutation):


    class Arguments:
        samples = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, samples, dataset, **kwargs):
        dataset = models.Experiment.objects.get(id=dataset)
        samples = models.Sample.objects.filter(id__in=samples)
        dataset.samples.remove(*samples)
        dataset.save()
        return dataset

    class Meta:
        type = types.Dataset

class PutRepresentations(BalderMutation):

    class Arguments:
        representations = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, representations, dataset, **kwargs):
        dataset = models.Dataset.objects.get(id=dataset)
        representations = models.Representation.objects.filter(id__in=representations)
        dataset.representations.add(*representations)
        return dataset

    class Meta:
        type = types.Dataset

class ReleaseRepresentations(BalderMutation):


    class Arguments:
        representations = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, representations, dataset, **kwargs):
        dataset = models.Experiment.objects.get(id=dataset)
        representations = models.Representation.objects.filter(id__in=representations)
        dataset.representations.remove(*representations)
        dataset.save()
        return dataset

    class Meta:
        type = types.Dataset
        

class PutFiles(BalderMutation):


    class Arguments:
        files = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, files, dataset, **kwargs):
        dataset = models.Dataset.objects.get(id=dataset)
        files = models.OmeroFile.objects.filter(id__in=files)
        dataset.omerofiles.add(*files)
        dataset.save()
        return dataset

    class Meta:
        type = types.Dataset
        

class ReleaseFiles(BalderMutation):


    class Arguments:
        files = graphene.List(graphene.ID, required=True)
        dataset = graphene.ID(required=True)

    @bounced()
    def mutate(root, info, files, dataset, **kwargs):
        dataset = models.Dataset.objects.get(id=dataset)
        files = models.OmeroFile.objects.filter(id__in=files)
        dataset.omerofiles.remove(*files)
        dataset.save()
        return dataset

    class Meta:
        type = types.Dataset