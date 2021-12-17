from balder.types.query.base import BalderQuery
import graphene
from grunnlag.filters import ExperimentFilter, MetricFilter
from grunnlag import types, models


class Metric(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Metric.objects.get(id=id)

    class Meta:
        type = types.Metric
        operation = "metric"


class Metrics(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Metric
        filter = MetricFilter
        operation = "metrics"
        paginate = True
