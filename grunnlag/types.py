

from django.contrib.auth import get_user_model
from grunnlag.filters import RepresentationFilter
from balder.fields.filtered import BalderFiltered
from balder.types.object import BalderObject
import graphene
from .models import Representation, Experiment, Sample
from taggit.managers import TaggableManager
from graphene_django.converter import convert_django_field


class Tag(graphene.String):
    pass


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(Tag, description=field.help_text, required=not field.null)



class RepresentationType(BalderObject):
    """ A Representation is a multi-dimensional Array that can do what ever it wants """

    class Meta:
        model = Representation
        description = Representation.__doc__

class ExperimentType(BalderObject):

    class Meta:
        model = Experiment
        description = Experiment.__doc__

class SampleType(BalderObject):
    representations = BalderFiltered(RepresentationType, filterset_class=RepresentationFilter, related_field="representations")

    class Meta:
        model = Sample
        description = Sample.__doc__


class User(BalderObject):

    class Meta:
        model = get_user_model()
        description = get_user_model().__doc__