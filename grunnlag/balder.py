from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import ExperimentFilter, RepresentationFilter, SampleFilter
import graphene
import grunnlag.mutations
#import grunnlag.subscriptions TODO: This is currently breaking because of django-graphql-ws bad references


class MyRepresentations(BalderQuery):
    """ My Representations returns all of the Representations, attached to the current user
    """

    class Meta:
        list = True
        personal = "sample__creator"
        type = types.Representation
        filter = RepresentationFilter
        operation = "myrepresentations"

        
class MySamples(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = types.Sample
        filter = SampleFilter
        operation = "mysamples"

class MyExperiments(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = types.Experiment
        filter = ExperimentFilter
        operation = "myexperiments"


class Samples(BalderQuery):
    """ All Samples
    """
    class Meta:
        list = True
        type = types.Sample
        filter = SampleFilter
        operation = "samples"


class Representations(BalderQuery):
    """ All represetations
    """
    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        operation = "representations"


class ExperimentDetail(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Experiment.objects.get(id=id)
    
    class Meta:
        type= types.Experiment 
        operation = "experiment"   




class Representation(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Representation.objects.get(id=id)
    
    class Meta:
        type= types.Representation 
        operation = "representation"   



class Sample(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: models.Sample.objects.get(id=id)
    
    class Meta:
        type= types.Sample 
        operation = "sample"   


