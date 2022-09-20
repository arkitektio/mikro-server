from typing import Dict, Tuple
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from plotql import models, types
from lok import bounced
import logging


class CreatePlot(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        name = graphene.String(required=True, description="The name of the plot")

    @bounced()
    def mutate(
        root,
        info,
        name=None,
    ):
        creator = info.context.user

        exp = models.Plot.objects.create(name=name, creator=creator)
        exp.save()

        return exp

    class Meta:
        type = types.Plot


class UpdatePlot(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(required=True, description="The name of the plot")
        query = graphene.String(description="The query of the plot")
        name = graphene.String(description="The query of the plot")

    @bounced()
    def mutate(
        root,
        info,
        id,
        name=None,
        query=None,
    ):
        creator = info.context.user

        exp = models.Plot.objects.get(id=id)
        exp.name = name or exp.name
        exp.query = query
        exp.save()

        return exp

    class Meta:
        type = types.Plot


class DeletePlotResult(graphene.ObjectType):
    id = graphene.String()


class DeletePlot(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="A cleartext description what this representation represents as data",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        sample = models.Plot.objects.get(id=id)
        sample.delete()
        return {"id": id}

    class Meta:
        type = DeletePlotResult
