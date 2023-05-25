from django.contrib.auth import get_user_model
from lok import bounced
from balder.types import BalderMutation
import graphene
from bord import models
from bord.scalar import ParquetInput
from grunnlag.scalars import AssignationID
from grunnlag import types
import logging
import namegenerator
from graphene.types.generic import GenericScalar
from balder.types.scalars import ImageFile
from balder.types.mutation import BalderMutation
from grunnlag.utils import fill_created


class CreateGraph(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        image = ImageFile(
            required=True,
            description="The image of the graph",
        )
        tables = graphene.List(
            graphene.ID,
            required=False,
            description="The tables that make up the graph",
        )
        name = graphene.String(
            required=False,
            description="A cleartext description what this representation represents as data",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )
        columns = graphene.List(
            graphene.String,
            required=False,
            description="The colums of the table that make up the graph",
        )
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(root, info, *args, creator=None, tables = None, created_while = None, image=None, columns = None,  **kwargs):
        name = kwargs.pop("name", namegenerator.gen())
        tags = kwargs.pop("tags", [])
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        graph = models.Graph.objects.create(
            name=name,
            image=image,
            used_columns=columns,
            **fill_created(info),
            created_while=created_while,
        )

        if tags:
            graph.tags.add(*tags)

        if tables:
            graph.tables.set(models.Table.objects.filter(id__in=tables).all())


        graph.save()

        return graph

    class Meta:
        type = types.Graph




class DeleteGraphResult(graphene.ObjectType):
    id = graphene.String()


class DeleteGraph(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deletet Representation", required=True
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        tab = models.Graph.objects.get(id=id)
        tab.delete()
        return {"id": id}

    class Meta:
        type = DeleteGraphResult
