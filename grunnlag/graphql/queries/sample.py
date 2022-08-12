from balder.types.query.base import BalderQuery
from grunnlag import types, models
import graphene
from grunnlag.filters import ExperimentFilter, SampleFilter
from grunnlag import types, models


class Sample(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        rep = models.Sample.objects.get(id=id)
        assert rep.creator == info.context.user or info.context.user.has_perm(
            "grunnlag.view_sample", rep
        ), "You do not have permission to view this sammple"

        return rep

    class Meta:
        type = types.Sample
        operation = "sample"


class Samples(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Sample
        filter = SampleFilter
        operation = "samples"
        paginate = True


class MySamples(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Sample
        filter = SampleFilter
        paginate = True
        operation = "mysamples"
