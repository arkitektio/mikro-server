from balder.types.query.base import BalderQuery
from grunnlag import types, models
import graphene
from grunnlag.filters import ExperimentFilter, SampleFilter
from grunnlag import types, models


class Sample(BalderQuery):
    """Get a Sample by ID
    
    Returns a single Sample by ID. If the user does not have access
    to the Sample, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        rep = models.Sample.objects.get(id=id)

        return rep

    class Meta:
        type = types.Sample
        operation = "sample"


class Samples(BalderQuery):
    """All Samples
    
    This query returns all Samples that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Samples that the user has access to. If the user is an amdin
    or superuser, all Samples will be returned.
    
    """

    class Meta:
        list = True
        type = types.Sample
        filter = SampleFilter
        operation = "samples"
        paginate = True


class MySamples(BalderQuery):
    """My Samples runs a fast query on the database to return all
    Samples that the user has *created*. This query is faster than
    the `samples` query, but it does not return all Samples that
    the user has access to."""

    class Meta:
        list = True
        personal = "creator"
        type = types.Sample
        filter = SampleFilter
        paginate = True
        operation = "mysamples"
