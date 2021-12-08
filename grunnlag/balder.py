from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    ExperimentFilter,
    MetricFilter,
    OmeroFileFilter,
    RepresentationFilter,
    SampleFilter,
    TagFilter,
)
import graphene
import grunnlag.mutations
import grunnlag.subscriptions
from graphene.types.generic import GenericScalar
from lok import bounced


class Negotiate(BalderMutation):
    class Arguments:
        additionals = GenericScalar(description="Additional Parameters")
        internal = graphene.Boolean(description="is this now a boolean")

    @bounced(only_jwt=True)
    def mutate(root, info, *args, internal=False, additionals={}):
        host = info.context.get_host().split(":")[0] if not internal else "minio"

        return {
            "protocol": "s3",
            "path": f"{host}:9000",
            "params": {
                "access_key": "weak_access_key",
                "secret_key": "weak_secret_key",
            },
        }

    class Meta:
        type = GenericScalar
        operation = "negotiate"


class MyRepresentations(BalderQuery):
    """My Representations returns all of the Representations, attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "myrepresentations"


class MyOmeroFiles(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "myomerofiles"


class OmeroFiles(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "omerofiles"


class MySamples(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Sample
        filter = SampleFilter
        paginate = True
        operation = "mysamples"


class MyExperiments(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Experiment
        filter = ExperimentFilter
        paginate = True
        operation = "myexperiments"


class Samples(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Sample
        filter = SampleFilter
        operation = "samples"
        paginate = True


class Metrics(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Metric
        filter = MetricFilter
        operation = "metrics"
        paginate = True


class Tags(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Tag
        filter = TagFilter
        operation = "tags"
        paginate = True


class Experiments(BalderQuery):
    """All Samples"""

    class Meta:
        list = True
        type = types.Experiment
        filter = ExperimentFilter
        operation = "experiments"
        paginate = True


class Representations(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "representations"


class ExperimentDetail(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Experiment.objects.get(id=id)

    class Meta:
        type = types.Experiment
        operation = "experiment"


class Representation(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Representation.objects.get(id=id)

    class Meta:
        type = types.Representation
        operation = "representation"


class OmeroFile(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.OmeroFile.objects.get(id=id)

    class Meta:
        type = types.OmeroFile
        operation = "omerofile"


class Metric(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Metric.objects.get(id=id)

    class Meta:
        type = types.Metric
        operation = "metric"


class Sample(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Sample.objects.get(id=id)

    class Meta:
        type = types.Sample
        operation = "sample"
