from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ExperimentFilter
from grunnlag import types, models


class Experiments(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Experiment
        filter = ExperimentFilter
        operation = "experiments"
        paginate = True


class ExperimentDetail(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        x = models.Experiment.objects.get(id=id)
        # assert (
        #     info.context.user.has_perm("grunnlag.view_experiment", x)
        #     or x.creator == info.context.user
        # ), "You do not have permission to view this representation"

        return x

    class Meta:
        type = types.Experiment
        operation = "experiment"


class MyExperiments(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Experiment
        filter = ExperimentFilter
        paginate = True
        operation = "myexperiments"
