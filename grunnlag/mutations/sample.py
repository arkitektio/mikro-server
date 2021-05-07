from herre.bouncer.utils import bounced
from balder.types import BalderMutation
import graphene
from grunnlag import models, types

class CreateSample(BalderMutation):
    """Creates a Sample
    """

    class Arguments:
        name = graphene.String(required=True, description="A cleartext name for this Sample")
        experiments = graphene.List(graphene.ID, required=False, description="The Experiments this sample Belongs to")


    @bounced(anonymous=False)
    def mutate(root, info, *args, **kwargs):
        experiments = kwargs.get("experiments", None)
        name = kwargs.get("name", None)
        sample = models.Sample.objects.create(creator = info.context.user, name=name)
        if experiments: sample.experiments.add(*experiments)

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