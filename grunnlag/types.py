

from django.contrib.auth import get_user_model
from grunnlag.filters import RepresentationFilter
from balder.fields.filtered import BalderFiltered
from balder.types.object import BalderObject
import graphene
from grunnlag import models
from taggit.managers import TaggableManager
from graphene_django.converter import convert_django_field


class Tag(graphene.String):
    pass


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(Tag, description=field.help_text, required=not field.null)



class Representation(BalderObject):
    """ A Representation is a multi-dimensional Array that can do what ever it wants """

    class Meta:
        model = models.Representation
        description = models.Representation.__doc__

class Experiment(BalderObject):

    class Meta:
        model = models.Experiment
        description = models.Experiment.__doc__

class Sample(BalderObject):
    representations = BalderFiltered(Representation, filterset_class=RepresentationFilter, related_field="representations")

    class Meta:
        model = models.Sample
        description = models.Sample.__doc__


class User(BalderObject):

    class Meta:
        model = get_user_model()
        description = get_user_model().__doc__