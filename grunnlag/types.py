from django.contrib.auth import get_user_model
from bord.filters import TableFilter
from grunnlag.omero import Channel, OmeroRepresentation, PhysicalSize, Plane
from graphene.types.generic import GenericScalar
from grunnlag.filters import (
    RepresentationFilter,
    RepresentationMetricFilter,
    SampleFilter,
)
from balder.fields.filtered import BalderFiltered
from balder.fields.offsetfiltered import BalderFilteredWithOffset
from balder.types.object import BalderObject
import graphene
from grunnlag import models
from taggit.managers import TaggableManager
from graphene_django.converter import convert_django_field
from django.conf import settings
from grunnlag.enums import OmeroFileType
from bord import models as bordmodels


class Tag(graphene.String):
    pass


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(Tag, description=field.help_text, required=not field.null)


class Thumbnail(BalderObject):
    def resolve_image(root, info, *args, **kwargs):
        try:
            host = info.context.get_host().split(":")[0]
        except:
            host = {
                key.decode("utf-8"): item.decode("utf-8")
                for key, item in info.context["headers"]
            }["host"].split(":")[0]
        port = 9000
        url = f"http://{host}:{port}"
        return (
            root.image.url.replace(settings.AWS_S3_ENDPOINT_URL, url)
            if root.image
            else None
        )

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


class OmeroFile(BalderObject):
    thumbnail = graphene.String(description="Url of a thumbnail")

    def resolve_thumbnail(root, info, *args, **kwargs):
        if root.type == OmeroFileType.MSR:
            pass

        try:
            host = info.context.get_host().split(":")[0]
        except:
            host = {
                key.decode("utf-8"): item.decode("utf-8")
                for key, item in info.context["headers"]
            }["host"].split(":")[0]
        port = 9000
        url = f"http://{host}:{port}/"
        return (
            root.file.url.replace(settings.AWS_S3_ENDPOINT_URL, url)
            if root.file
            else None
        )

    def resolve_file(root, info, *args, **kwargs):
        try:
            host = info.context.get_host().split(":")[0]
        except:
            host = {
                key.decode("utf-8"): item.decode("utf-8")
                for key, item in info.context["headers"]
            }["host"].split(":")[0]
        port = 9000
        url = f"http://{host}:{port}"
        return (
            root.file.url.replace(settings.AWS_S3_ENDPOINT_URL, url)
            if root.file
            else None
        )

    class Meta:
        model = models.OmeroFile


class Table(BalderObject):
    query = graphene.List(
        lambda: graphene.List(GenericScalar),
        description="List of List",
        columns=graphene.List(
            graphene.String, description="Columns you want to select", required=False
        ),
        offset=graphene.Int(required=False, description="The Offset for the query"),
        limit=graphene.Int(required=False, description="The Offset for the query"),
    )

    def resolve_query(root, info, *args, columns=[], offset=0, limit=200):
        return [["Empty Value" for column in columns] for i in range(limit)]

    class Meta:
        model = bordmodels.Table


class Representation(BalderObject):
    """A Representation is a multi-dimensional Array that can do what ever it wants


    @elements/rep:latest


    """

    metrics = BalderFilteredWithOffset(
        RepresentationMetric,
        filterset_class=RepresentationMetricFilter,
        related_field="metrics",
    )
    latest_thumbnail = graphene.Field(Thumbnail)
    omero = graphene.Field(OmeroRepresentation)
    tables = BalderFilteredWithOffset(
        Table,
        filterset_class=TableFilter,
        related_field="tables",
    )

    def resolve_latest_thumbnail(root, info, *args, **kwargs):
        return root.thumbnails.last()

    class Meta:
        model = models.Representation


class Sample(BalderObject):
    """Samples are storage containers for representations. A Sample is to be understood analogous to a Biological Sample. It existed in Time (the time of acquisiton and experimental procedure),
    was measured in space (x,y,z) and in different modalities (c). Sample therefore provide a datacontainer where each Representation of
    the data shares the same dimensions. Every transaction to our image data is still part of the original acuqistion, so also filtered images are refering back to the sample @elements/sample"""

    representations = BalderFilteredWithOffset(
        Representation,
        filterset_class=RepresentationFilter,
        related_field="representations",
    )

    class Meta:
        model = models.Sample
        description = models.Sample.__doc__


class Experiment(BalderObject):
    """A Representation is a multi-dimensional Array that can do what ever it wants @elements/experiment"""

    samples = BalderFilteredWithOffset(
        Sample, filterset_class=SampleFilter, related_field="samples"
    )

    class Meta:
        model = models.Experiment


class User(BalderObject):
    class Meta:
        model = get_user_model()
        description = get_user_model().__doc__
