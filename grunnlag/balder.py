from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import ExperimentFilter, RepresentationFilter, RepresentationMetricFilter, SampleFilter
import graphene
import grunnlag.mutations
import grunnlag.subscriptions
from graphene.types.generic import GenericScalar
from delt.registry.nodes import registry
from delt.management.commands.register_point import parse_data_models
#import


from herre import bounced




class Negotiate(BalderMutation):

    class Arguments:
        additionals = GenericScalar(description="Additional Parameters")
        internal = graphene.Boolean(description="is this now a boolean")

    @bounced(only_jwt=True)
    def mutate(root, info, *args,internal=False, additionals={}):
        host = info.context.get_host().split(":")[0] if not internal else "minio"

        return {
        "protocol": "s3",
        "path": f"{host}:9000",
        "params": {
            "access_key": "weak_access_key",
            "secret_key": "weak_secret_key"
        }
        }

    class Meta:
        type = GenericScalar
        operation = "negotiate"









class MyRepresentations(BalderQuery):
    """ My Representations returns all of the Representations, attached to the current user
    """

    class Meta:
        list = True
        personal = "sample__creator"
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "myrepresentations"

        
class MySamples(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = types.Sample
        filter = SampleFilter
        paginate = True
        operation = "mysamples"

class MyExperiments(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = types.Experiment
        filter = ExperimentFilter
        paginate = True
        operation = "myexperiments"


class Samples(BalderQuery):
    """ All Samples
    """
    class Meta:
        list = True
        type = types.Sample
        filter = SampleFilter
        operation = "samples"
        paginate = True


class RepresentationMetric(BalderQuery):
    """ All Samples
    """
    class Meta:
        list = True
        type = types.RepresentationMetric
        filter = RepresentationMetricFilter
        operation = "metrics"
        paginate = True


class Experiments(BalderQuery):
    """ All Samples
    """
    class Meta:
        list = True
        type = types.Experiment
        filter = ExperimentFilter
        operation = "experiments"
        paginate = True


class Representations(BalderQuery):
    """ All represetations
    """
    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "representations"


class ExperimentDetail(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Experiment.objects.get(id=id)
    
    class Meta:
        type= types.Experiment 
        operation = "experiment"   


class Nodes(BalderQuery):


    @bounced()
    def resolve(root, info):

        registry.node_dicts.values()
        return registry.node_dicts.values()


    class Meta:
        list = True
        type = types.Node
        operation = "_nodes"


class Models(BalderQuery):

    @bounced()
    def resolve(root, info):
        return parse_data_models()

    class Meta:
        list = True
        type = types.DataModel
        operation = "_models"



class Representation(BalderQuery):
    """ Get a single representation by ID 
    
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Representation.objects.get(id=id)
    
    class Meta:
        type= types.Representation 
        operation = "representation"   


class Metric(BalderQuery):
    """ Get a single representation by ID 
    
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.RepresentationMetric.objects.get(id=id)
    
    class Meta:
        type= types.RepresentationMetric 
        operation = "metric" 


class Sample(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Sample.objects.get(id=id)
    
    class Meta:
        type= types.Sample 
        operation = "sample"   


