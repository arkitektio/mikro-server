

from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from grunnlag.filters import RepresentationFilter, RepresentationMetricFilter, SampleFilter
from balder.fields.filtered import BalderFiltered
from balder.fields.offsetfiltered import BalderFilteredWithOffset
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




class DataModel(graphene.ObjectType):
    identifier = graphene.String(description="Name")
    extenders = graphene.List(graphene.String)


class Node(graphene.ObjectType):
    name = graphene.String(description="Name")
    interface = graphene.String(description="Name")
    package = graphene.String(description="Name")
    type = graphene.String(description="Name")
    args = graphene.List(GenericScalar)
    kwargs = graphene.List(GenericScalar)
    returns = graphene.List(GenericScalar)



class RepresentationMetric(BalderObject):

    class Meta:
        model = models.RepresentationMetric


class Representation(BalderObject):
    """ A Representation is a multi-dimensional Array that can do what ever it wants
    

     @elements/rep:latest   


     """
    metrics = BalderFilteredWithOffset(RepresentationMetric, filterset_class=RepresentationMetricFilter, related_field="metrics")




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




class Sample(BalderObject):
    """ Samples are storage containers for representations. A Sample is to be understood analogous to a Biological Sample. It existed in Time (the time of acquisiton and experimental procedure),
    was measured in space (x,y,z) and in different modalities (c). Sample therefore provide a datacontainer where each Representation of
    the data shares the same dimensions. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample @elements/sample"""
    representations = BalderFilteredWithOffset(Representation, filterset_class=RepresentationFilter, related_field="representations")

    class Meta:
        model = models.Sample
        description = models.Sample.__doc__

class Experiment(BalderObject):
    """ A Representation is a multi-dimensional Array that can do what ever it wants @elements/experiment"""
    samples = BalderFilteredWithOffset(Sample, filterset_class=SampleFilter, related_field="samples")


    class Meta:
        model = models.Experiment


class User(BalderObject):

    class Meta:
        model = get_user_model()
        description = get_user_model().__doc__