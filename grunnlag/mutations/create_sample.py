from herre.bouncer.utils import bounced
from balder.types import BalderMutation
import graphene
from grunnlag import models, types

class CreateSample(BalderMutation):
    """Creates a Sample
    """

    class Arguments:
        name = graphene.String(required=True, description="A cleartext name for this Sample")
        experiment = graphene.ID(required=False, description="The Experiment this Sample Belongs to")


    @bounced(anonymous=False)
    def mutate(root, info, *args, **kwargs):
        experiment = kwargs.get("experiment", None)
        name = kwargs.get("name", None)
        sample = models.Sample.objects.create(creator = info.context.user, experiment_id=experiment, name=name)
        return sample


    class Meta:
        type = types.Sample