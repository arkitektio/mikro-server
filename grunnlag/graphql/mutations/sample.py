from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
import namegenerator
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag import models, types

from grunnlag.scalars import AssignationID

class CreateSample(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        name = graphene.String(
            required=False, description="A cleartext name for this Sample"
        )
        experiments = graphene.List(
            graphene.ID,
            required=False,
            description="The Experiments this sample Belongs to",
        )
        creator = graphene.String(
            required=False, description="The email of the creator, only for backend app"
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )
        created_while = AssignationID(required=False, description="The assignation id")
        meta = GenericScalar(required=False, description="Meta Parameters")

    @bounced(anonymous=False)
    def mutate(
        root,
        info,
        experiments=[],
        name=None,
        creator=None,
         created_while=None,
        tags=[],
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        sample = models.Sample.objects.create(
            creator=creator, created_while=created_while, name=name or namegenerator.gen()
        )
        if experiments:
            sample.experiments.add(*experiments)
        if tags:
            sample.tags.add(*tags)

        return sample

    class Meta:
        type = types.Sample


class UpdateSample(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String(
            required=False, description="A cleartext name for this Sample"
        )
        experiments = graphene.List(
            graphene.ID,
            required=False,
            description="The Experiments this sample Belongs to",
        )
        tags = graphene.List(
            graphene.String,
            required=False,
            description="Do you want to tag the representation?",
        )
        meta = GenericScalar(required=False, description="Meta Parameters")

    @bounced(anonymous=False)
    def mutate(
        root,
        info,
        id,
        experiments=[],
        name=None,
        creator=None,
        tags=[],
    ):
        creator = info.context.user or (
            get_user_model().objects.get(email=creator) if creator else None
        )

        sample = models.Sample.objects.get(id=id)

        if name:
            sample.name = name
        if experiments:
            sample.experiments.set(*experiments)
        if tags:
            sample.tags.add(*tags)

        sample.save()
        return sample

    class Meta:
        type = types.Sample


class DeleteSampleResult(graphene.ObjectType):
    id = graphene.String()


class DeleteSample(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="A cleartext description what this representation represents as data",
            required=True,
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        sample = models.Sample.objects.get(id=id)
        sample.delete()
        return {"id": id}

    class Meta:
        type = DeleteSampleResult


class PinSample(BalderMutation):
    """Sets the pin"""

    class Arguments:
        id = graphene.ID(required=True, description="The ID of the representation")
        pin = graphene.Boolean(required=True, description="The pin")

    @bounced()
    def mutate(root, info, id, pin, **kwargs):
        rep = models.Sample.objects.get(id=id)
        if pin:
            rep.pinned_by.add(info.context.user)
        else:
            rep.pinned_by.remove(info.context.user)
        rep.save()
        return rep

    class Meta:
        type = types.Sample


