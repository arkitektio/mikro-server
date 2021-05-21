

from django.contrib.auth import get_user_model
from grunnlag.filters import RepresentationFilter
from balder.fields.filtered import BalderFiltered
from balder.types.object import BalderObject
import graphene
from grunnlag import models
from taggit.managers import TaggableManager
from graphene_django.converter import convert_django_field
from django.conf import settings

class Tag(graphene.String):
    pass


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(Tag, description=field.help_text, required=not field.null)


class Thumbnail(BalderObject):

    class Meta:
        model = models.Thumbnail
        description = models.Thumbnail.__doc__




class Representation(BalderObject):
    """ A Representation is a multi-dimensional Array that can do what ever it wants """

    def resolve_thumbnail(root, info,*args, **kwargs):
        try:
            host = info.context.get_host().split(":")[0]
        except:
            host = {key.decode("utf-8"): item.decode("utf-8") for key, item in info.context["headers"]}["host"].split(":")[0]
        port = 9000
        url = f"http://{host}:{port}"
        return root.thumbnail.url.replace(settings.AWS_S3_ENDPOINT_URL, url) if root.thumbnail else None

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