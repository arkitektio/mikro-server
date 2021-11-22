from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
import namegenerator
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag import models, types

class CreateSample(BalderMutation):
    """Creates a Sample
    """

    class Arguments:
        name = graphene.String(required=False, description="A cleartext name for this Sample")
        experiments = graphene.List(graphene.ID, required=False, description="The Experiments this sample Belongs to")
        creator = graphene.String(required=False, description="The email of the creator, only for backend app")
        tags = graphene.List(graphene.String, required=False, description="Do you want to tag the representation?")
        meta = GenericScalar(required=False,description="Meta Parameters")


    @bounced(anonymous=False)
    def mutate(root, info, experiments = [], name= namegenerator.gen(), creator=None, meta=None, tags=[]):
        creator = info.context.user or (get_user_model().objects.get(email=creator) if creator else None)
        
        sample = models.Sample.objects.create(creator = creator, name=name, meta=meta)
        if experiments: sample.experiments.add(*experiments)
        if tags: sample.tags.add(*tags)

        return sample


    class Meta:
        type = types.Sample

class DeleteSampleResult(graphene.ObjectType):
    id = graphene.String()


class DeleteSample(BalderMutation):
    """ Create an experiment (only signed in users)
    """

    class Arguments:
        id = graphene.ID(description="A cleartext description what this representation represents as data", required=True)


    @bounced()
    def mutate(root, info, id, **kwargs):
        sample =  models.Sample.objects.get(id=id)
        sample.delete()
        return {"id": id}


    class Meta:
        type = DeleteSampleResult