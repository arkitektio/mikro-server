from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from lok import bounced
from grunnlag.utils import fill_created

class CreateContext(BalderMutation):
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
        experiment = graphene.ID(
            required=False,
            description="The experiment this context belongs to",
        )

    @bounced()
    def mutate(
        root, info, name=None, description=None, creator=None, tags=[], experiment=None
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        exp = models.Context.objects.create(
            creator=creator, name=name, experiment_id=experiment, **fill_created(info)
        )

        if tags:
            exp.tags.add(*tags)
        return exp

    class Meta:
        type = types.Context


class DeleteContextResult(graphene.ObjectType):
    id = graphene.String()


class DeleteContext(BalderMutation):
    """Delete Experiment
    
    This mutation deletes an Experiment and returns the deleted Experiment."""

    class Arguments:
        id = graphene.ID(
            description="The ID of the experiment to delete",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        experiment = models.Context.objects.get(id=id)
        experiment.delete()
        return {"id": id}

    class Meta:
        type = DeleteContextResult