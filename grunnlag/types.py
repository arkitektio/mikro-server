from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import FileField
from graphene.types.scalars import String
from graphene_django import DjangoObjectType
from bord.filters import TableFilter
from grunnlag.scalars import FeatureValue, File, MetricValue, Parquet, Store
from grunnlag.omero import (
    Channel,
    ImagingEnvironment,
    ObjectiveSettings,
    PhysicalSize,
    Plane,
)
from graphene.types.generic import GenericScalar
from grunnlag.filters import (
    FeatureFilter,
    MetricFilter,
    ROIFilter,
    RepresentationFilter,
    SampleFilter,
)
from bord.enums import PandasDType
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
from komment.types import Comment


class Tag(BalderObject):
    class Meta:
        model = Tag


@convert_django_field.register(TaggableManager)
def convert_field_to_string(field, registry=None):
    return graphene.List(String, description=field.help_text, required=not field.null)


@convert_django_field.register(models.OmeroFileField)
def convert_field_to_string(field, registry=None):
    return graphene.Field(File, description=field.help_text, required=not field.null)


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
    value = MetricValue(description="Value")

    class Meta:
        model = models.Metric


class OmeroFile(BalderObject):
    thumbnail = graphene.String(description="Url of a thumbnail")

    def resolve_file(root, info, *args, **kwargs):
        return root.file.url if root.file else None

    class Meta:
        model = models.OmeroFile


class Column(graphene.ObjectType):
    """ A column in a table

    A Column describes the associated name and metadata of a column in a table.
    It gives access to the pandas and numpy dtypes of the column.

    """

    name = graphene.String(description="The Column Name")
    field_name = graphene.String(description="The FIeld Name", required=True)
    pandas_type = graphene.Field(
        PandasDType, description="The Panda Types for the Column"
    )
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
        limit=graphene.Int(required=False, description="The Limit for the query"),
        query=graphene.String(required=False, description="The Query for the query"),
    )
    store = graphene.Field(Parquet, description="The parquet store for the table")
    columns = graphene.List(
        Column,
        description="Columns Data",
        only=graphene.List(
            graphene.String, description="Columns you want to select", required=False
        ),
    )
    pinned = graphene.Boolean(description="Is the table pinned by the active user")

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

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
        description = bordmodels.Table.__doc__


class Instrument(BalderObject):
    class Meta:
        model = models.Instrument
        description = models.Instrument.__doc__


class Omero(BalderObject):
    planes = graphene.List(Plane)
    channels = graphene.List(Channel)
    physical_size = graphene.Field(PhysicalSize)
    scale = graphene.List(graphene.Float)
    acquisition_date = graphene.DateTime()
    imaging_environment = graphene.Field(ImagingEnvironment)
    objective_settings = graphene.Field(ObjectiveSettings)

    class Meta:
        model = models.Omero
        description = models.Omero.__doc__


class Representation(BalderObject):

    identifier: str = graphene.String(description="The Arkitekt identifier")
    metrics = BalderFilteredWithOffset(
        Metric,
        filterset_class=MetricFilter,
        related_field="metrics",
        description="Associated metrics of this Image",
    )
    latest_thumbnail = graphene.Field(Thumbnail)
    store = graphene.Field(Store)
    tables = BalderFilteredWithOffset(
        Table,
        filterset_class=TableFilter,
        related_field="tables",
        description="Associated tables",
    )
    rois = BalderFilteredWithOffset(
        lambda: ROI,
        model=models.ROI,
        filterset_class=ROIFilter,
        related_field="rois",
        description="Associated rois",
    )
    derived = BalderFilteredWithOffset(
        lambda: Representation,
        model=models.Representation,
        filterset_class=RepresentationFilter,
        related_field="derived",
        description="Derived Images from this Image",
    )
    comments = graphene.List(Comment)
    pinned = graphene.Boolean()

    def resolve_latest_thumbnail(root, info, *args, **kwargs):
        return root.thumbnails.last()

    def resolve_store(root, info, *args, **kwargs):
        return root.store.get_path()

    def resolve_comments(root, info, *args, **kwargs):
        return root.comments.all()

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

    class Meta:
        model = models.Representation
        description = models.Representation.__doc__


class Sample(BalderObject):
    representations = BalderFilteredWithOffset(
        Representation,
        filterset_class=RepresentationFilter,
        related_field="representations",
        description="Associated representations of this Sample",
    )
    pinned = graphene.Boolean()

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

    class Meta:
        model = models.Sample
        description = models.Sample.__doc__


class Experiment(BalderObject):

    samples = BalderFilteredWithOffset(
        Sample, filterset_class=SampleFilter, related_field="samples"
    )
    pinned = graphene.Boolean()

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

    class Meta:
        model = models.Experiment
        description = models.Experiment.__doc__


class Vector(graphene.ObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")
    t = graphene.Float(description="T-coordinate")
    c = graphene.Float(description="C-coordinate")


class ROI(BalderObject):
    vectors = graphene.List(Vector, description="The vectors of the ROI")

    pinned = graphene.Boolean(description="Is the ROI pinned by the active user")

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

    class Meta:
        model = models.ROI
        description = models.ROI.__doc__


class Label(BalderObject):
    features = BalderFilteredWithOffset(
        lambda: Feature,
        model=models.Feature,
        filterset_class=FeatureFilter,
        related_field="features",
        description="Features attached to this Label",
    )
    feature = graphene.Field(
        lambda: Feature,
        key=graphene.String(required=True),
    )

    pinned = graphene.Boolean()

    def resolve_pinned(root, info, *args, **kwargs):
        return root.pinned_by.filter(id=info.context.user.id).exists()

    def resolve_feature(root, info, key):
        return root.features.get(key=key)

    class Meta:
        model = models.Label
        description = models.Label.__doc__


class Feature(BalderObject):
    value = FeatureValue(description="Value")

    class Meta:
        model = models.Feature
        description = models.Feature.__doc__
