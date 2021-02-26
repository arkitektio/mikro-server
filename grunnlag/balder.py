from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
from grunnlag.types import ExperimentType, RepresentationType, SampleType
from balder.types.query.base import BalderQuery
from grunnlag.filters import ExperimentFilter, RepresentationFilter, SampleFilter
import graphene
from grunnlag.models import Representation, Sample
import grunnlag.mutations
import grunnlag.subscriptions


class MyRepresentations(BalderQuery):
    """ My Representations returns all of the Representations, attached to the current user
    """

    class Meta:
        list = True
        personal = "sample__creator"
        type = RepresentationType
        filter = RepresentationFilter
        operation = "myrepresentations"

        
class MySamples(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = SampleType
        filter = SampleFilter
        operation = "mysamples"

class MyExperiments(BalderQuery):
    """ My samples return all of the users samples attached to the current user
    """

    class Meta:
        list = True
        personal = "creator"
        type = ExperimentType
        filter = ExperimentFilter
        operation = "myexperiments"


class Samples(BalderQuery):
    """ All Samples
    """
    class Meta:
        list = True
        type = SampleType
        filter = SampleFilter
        operation = "samples"


class Representations(BalderQuery):
    """ All represetations
    """
    class Meta:
        list = True
        type = RepresentationType
        filter = RepresentationFilter
        operation = "representations"




class RepresentationByID(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: Representation.objects.get(id=id)
    
    class Meta:
        type= RepresentationType 
        operation = "representation"   



class SampleByID(BalderQuery):
    """ Get a single representation by ID """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)


    resolve = lambda root, info, id: Sample.objects.get(id=id)
    
    class Meta:
        type= SampleType 
        operation = "sample"   


