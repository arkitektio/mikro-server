from django.contrib.auth import get_user_model
from lok import bounced
from balder.types import BalderMutation
import graphene
from bord import models
from grunnlag import types
import logging
import namegenerator
from graphene.types.generic import GenericScalar


class UpdateTable(BalderMutation):
    """Updates an Representation (also retriggers meta-data retrieval from data stored in)"""

    class Arguments:
        id = graphene.ID(
            required=True, description="Which sample does this representation belong to"
        )

    @bounced()
    def mutate(root, info, *args, **kwargs):
        tab = models.Table.objects.get(id=kwargs.pop("id"))
        tab.save()
        return tab

    class Meta:
        type = types.Table


class CreateTable(BalderMutation):
    """Creates a Representation"""

    class Arguments:
        sample = graphene.ID(
            required=False,
            description="Which sample does this table belong to",
        )
        representation = graphene.ID(
            required=False,
            description="Which sample does this table belong to",
        )
        experiment = graphene.ID(
            required=False,
            description="Which sample does this table belong to",
        )
        name = graphene.String(
            required=False,
            description="A cleartext description what this representation represents as data",
        )
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
    def mutate(root, info, *args, creator=None, **kwargs):
        sampleid = kwargs.pop("sample", None)
        repid = kwargs.pop("representation", None)
        expid = kwargs.pop("experiment", None)
        name = kwargs.pop("name", namegenerator.gen())
        tags = kwargs.pop("tags", [])
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        tab = models.Table.objects.create(
            name=name,
            sample_id=sampleid,
            representation_id=repid,
            experiment_id=expid,
            creator=creator,
        )

        if tags:
            tab.tags.add(*tags)

        tab.save()

        return tab

    class Meta:
        type = types.Table


class DeleteTableResult(graphene.ObjectType):
    id = graphene.String()


class DeleteTable(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deletet Representation", required=True
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        tab = models.Table.objects.get(id=id)
        tab.delete()
        return {"id": id}

    class Meta:
        type = DeleteTableResult