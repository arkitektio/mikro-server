from balder.types.query.base import BalderQuery
import graphene
from grunnlag.filters import ExperimentFilter, MetricFilter
from grunnlag import types, models


class Metric(BalderQuery):
    """Get a single Metric by ID
    
    Returns a single Metric by ID. If the user does not have access
    to the Metric, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Metric.objects.get(id=id)

    class Meta:
        type = types.Metric
        operation = "metric"


class Metrics(BalderQuery):
    """All Metric
    
    This query returns all Metric that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Metric that the user has access to. If the user is an amdin
    or superuser, all Metric will be returned.
    """

    class Meta:
        list = True
        type = types.Metric
        filter = MetricFilter
        operation = "metrics"
        paginate = True
