from attr import has
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import FileField
from graphene.types.scalars import String
from graphene_django import DjangoObjectType
from bord.filters import TableFilter
from grunnlag.scalars import File, Parquet, Store
from grunnlag.omero import Channel, OmeroRepresentation, PhysicalSize, Plane
from graphene.types.generic import GenericScalar
from grunnlag.filters import (
    MetricFilter,
    RepresentationFilter,
    SampleFilter,
)
from balder.fields.filtered import BalderFiltered
from balder.fields.offsetfiltered import BalderFilteredWithOffset
from balder.types.object import BalderObject
import graphene
from grunnlag import models
from taggit.managers import TaggableManager
from taggit.models import Tag
from graphene_django.converter import convert_django_field
from django.conf import settings
from grunnlag.enums import OmeroFileType
from bord import models as bordmodels
import logging
from grunnlag.graphql.utils import AvailableModelsEnum
from django.contrib.auth.models import Group as GroupModel
from balder.registry import register_type


class Tag(BalderObject):
    class Meta:
        model = Tag


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(String, description=field.help_text, required=not field.null)


@convert_django_field.register(models.OmeroFileField)
def convert_field_to_string(field, registry=None):
    return graphene.Field(File, description=field.help_text, required=not field.null)


class User(BalderObject):
    class Meta:
        model = get_user_model()
        fields = [
            "id",
            "username",
            "email",
            "last_name",
            "first_name",
            "groups",
        ]


class Thumbnail(BalderObject):
    def resolve_image(root, info, *args, **kwargs):
        return root.image.url if root.image else None

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


class Metric(BalderObject):
    class Meta:
        model = models.Metric


class OmeroFile(BalderObject):
    thumbnail = graphene.String(description="Url of a thumbnail")

    def resolve_file(root, info, *args, **kwargs):
        return root.file.url if root.file else None

    class Meta:
        model = models.OmeroFile


class Column(graphene.ObjectType):
    name = graphene.String(description="The Column Name")
    field_name = graphene.String(description="The FIeld Name", required=True)
    pandas_type = graphene.String(description="The Panda Types for the Column")
    numpy_type = graphene.String(description="The Numpy Types for the Column")
    metadata = GenericScalar(description="Generic MetaData from Stuff")


class PandasMetaData(graphene.ObjectType):
    columns = graphene.List(Column)


class Table(BalderObject):
    query = graphene.List(
        GenericScalar,
        description="List of Records",
        only=graphene.List(
            graphene.String, description="Columns you want to select", required=False
        ),
        offset=graphene.Int(required=False, description="The Offset for the query"),
        limit=graphene.Int(required=False, description="The Offset for the query"),
        query=graphene.String(required=False, description="The Query for the query"),
    )
    store = graphene.Field(Parquet)
    columns = graphene.List(
        Column,
        description="Columns Data",
        only=graphene.List(
            graphene.String, description="Columns you want to select", required=False
        ),
    )

    def resolve_store(root, info, *args, **kwargs):
        return root.store.name

    def resolve_query(root, info, *args, columns=[], offset=0, limit=200, query=None):
        pd_thing = root.store.data.read_pandas().to_pandas()
        logging.error(pd_thing)
        pd_thing = pd_thing[columns] if columns else pd_thing

        if query:
            pd_thing = pd_thing.query(query)

        return pd_thing.iloc[offset : offset + limit].to_dict("records")

    def resolve_columns(root, info, only=[]):
        columns_data = root.store.data.read().schema.pandas_metadata["columns"]

        return (
            columns_data
            if not only
            else [el for el in columns_data if el["field_name"] in only]
        )

    class Meta:
        model = bordmodels.Table


descendent_map = lambda: {
    "MentionDescendent": MentionDescendent,
    "ParagraphDescendent": ParagraphDescendent,
    "Leaf": Leaf,
}


@register_type
class Node(graphene.Interface):
    children = graphene.List(lambda: Descendent)
    untyped_children = GenericScalar()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get("typename", None)

    def resolve_untyped_children(root, info):
        return root.get("children", [])


@register_type
class Descendent(graphene.Interface):
    typename = graphene.String()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get(instance.get("typename"), Leaf)


@register_type
class Leaf(graphene.ObjectType):
    bold = graphene.Boolean(description="Is this a bold leaf?")
    italic = graphene.Boolean(description="Is this a italic leaf?")
    code = graphene.Boolean(description="Is this a code leaf?")
    text = graphene.String(description="The text of the leaf")

    class Meta:
        interfaces = (Descendent,)


@register_type
class MentionDescendent(graphene.ObjectType):
    user = graphene.String(description="The user that is mentioned", required=True)

    class Meta:
        interfaces = (Node, Descendent)


@register_type
class ParagraphDescendent(graphene.ObjectType):
    size = graphene.String(description="The size of the paragraph", required=False)

    class Meta:
        interfaces = (Node, Descendent)


class Comment(BalderObject):
    descendents = graphene.List(Descendent)
    children = graphene.List(
        lambda: Comment,
        limit=graphene.Int(description="How many children to return"),
        offset=graphene.Int(description="The offset for the children"),
    )
    content_type = graphene.Field(AvailableModelsEnum)

    def resolve_children(root, info, *args, offset=0, limit=20):
        return root.children.order_by("-created_at")[offset : offset + limit]

    def resolve_content_type(root, info):
        ct = root.content_type
        return f"{ct.app_label}_{ct.model}".replace(" ", "_").upper()

    class Meta:
        model = models.Comment


class Representation(BalderObject):
    """A Representation is a multi-dimensional Array that can do what ever it wants


    @elements/rep:latest


    """

    metrics = BalderFilteredWithOffset(
        Metric,
        filterset_class=MetricFilter,
        related_field="metrics",
        description="Associated metrics of this Image",
    )
    latest_thumbnail = graphene.Field(Thumbnail)
    omero = graphene.Field(
        OmeroRepresentation, description="Metadata in Omero-compliant format"
    )
    store = graphene.Field(Store)
    tables = BalderFilteredWithOffset(
        Table,
        filterset_class=TableFilter,
        related_field="tables",
        description="Associated tables",
    )
    derived = BalderFilteredWithOffset(
        lambda: Representation,
        model=models.Representation,
        filterset_class=RepresentationFilter,
        related_field="derived",
        description="Derived Images from this Image",
    )
    comments = graphene.List(Comment)

    def resolve_latest_thumbnail(root, info, *args, **kwargs):
        return root.thumbnails.last()

    def resolve_store(root, info, *args, **kwargs):
        return root.store.get_path()

    def resolve_comments(root, info, *args, **kwargs):
        return root.comments.all()

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
    color = graphene.String(description="The associated color for this user")

    def resolve_color(root, info):
        if hasattr(root, "meta"):
            return root.meta.color
        return "#FF0000"

    class Meta:
        model = get_user_model()
        description = get_user_model().__doc__


class Vector(graphene.ObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")
    t = graphene.Float(description="T-coordinate")
    c = graphene.Float(description="C-coordinate")


class ROI(BalderObject):
    vectors = graphene.List(Vector)

    class Meta:
        model = models.ROI


class Label(BalderObject):
    class Meta:
        model = models.Label


class SizeFeature(BalderObject):
    class Meta:
        model = models.SizeFeature


class Permission(BalderObject):
    unique = graphene.String(description="Unique ID for this permission", required=True)

    def resolve_unique(root, info):
        return f"{root.content_type.app_label}.{root.codename}"

    class Meta:
        model = Permission


class Group(BalderObject):
    class Meta:
        model = GroupModel
