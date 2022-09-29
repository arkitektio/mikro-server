from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from lok import bounced


class CreateExperiment(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        name = graphene.String(
            required=True,
            description="A cleartext description what this representation represents as data",
        )
        description = graphene.String(
            required=False, description="A short description of the experiment"
        )
        meta = GenericScalar(required=False, description="Meta Parameters")
        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )

    @bounced()
    def mutate(
        root, info, name=None, description=None, meta=None, creator=None, tags=[]
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Experiment.objects.create(
            creator=creator, description=description, meta=meta, name=name
        )
        if tags:
            exp.tags.add(*tags)
        return exp

    class Meta:
        type = types.Experiment


class DeleteExperimentResult(graphene.ObjectType):
    id = graphene.String()


class DeleteExperiment(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="A cleartext description what this representation represents as data",
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
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String(
            required=True,
            description="A cleartext description what this representation represents as data",
        )
        description = graphene.String(
            required=False, description="A short description of the experiment"
        )
        meta = GenericScalar(required=False, description="Meta Parameters")
        creator = graphene.String(
            required=False,
            description="The Email of the user creating the Representation (only for backend apps)",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
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
    """Sets the pin"""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin")

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
