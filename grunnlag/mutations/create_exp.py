from balder.types import BalderMutation
import graphene
from grunnlag import models, types
from herre.bouncer.utils import bounced



class CreateExperiment(BalderMutation):
    """ Create an experiment (only signed in users)
    """

    class Arguments:
        name = graphene.String(required=True, description="A cleartext description what this representation represents as data")
        description = graphene.String(required=False, description="A short description of the experiment")


    @bounced()
    def mutate(root, info, *args, **kwargs):
        return models.Experiment.objects.create(creator = info.context.user, **kwargs)


    class Meta:
        type = types.Experiment