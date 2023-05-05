from attr import field
import graphene
from balder.enum import InputEnum
import django_filters
from taggit.models import Tag
from .models import (
    ROI,
    Experiment,
    Instrument,
    Label,
    Metric,
    OmeroFile,
    Representation,
    Sample,
    Stage,
    Omero,
    Context,
)
from .enums import RepresentationVariety, RepresentationVarietyInput, RoiTypeInput, Dimension
from .linke import LinkableModels, linkable_models
from django import forms
from graphene_django.forms.converter import convert_form_field
from balder.filters import EnumFilter, MultiEnumFilter
import json
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType


class PinnedFilterMixin(django_filters.FilterSet):
    pinned = django_filters.BooleanFilter(
        method="my_pinned_filter", label="Filter by pinned"
    )

    def my_pinned_filter(self, queryset, name, value):
        if value:
            if (
                self.request
                and self.request.user
                and self.request.user.is_authenticated
            ):
                # needs to be checked becaust request is not ensured to be set
                return queryset.filter(pinned_by=self.request.user)
            else:
                raise Exception("Pin can only be used by authenticated users")
        else:
            return queryset


class IDChoiceField(forms.JSONField):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def overwritten_type(self, **kwargs):
        return graphene.List(graphene.ID, **kwargs)


@convert_form_field.register(IDChoiceField)
def convert_form_field_to_string_list(field):
    return field.overwritten_type(required=field.required)


class IDChoiceFilter(django_filters.MultipleChoiceFilter):
    field_class = IDChoiceField

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, field_name="pk")


class IdsFilter(django_filters.FilterSet):
    ids = IDChoiceFilter(label="Filter by values")

    def my_values_filter(self, queryset, name, value):
        if value:
            return queryset.filter(id__in=value)
        else:
            return queryset


class AppFilterMixin(django_filters.FilterSet):
    app = django_filters.CharFilter(
        method="my_app_filter", label="Created through which app"
    )

    def my_app_filter(self, queryset, name, value):
        return queryset.filter(created_through__app__identifier=value)


class TimeFilterMixin(django_filters.FilterSet):
    created_after = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    created_at = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    created_day = django_filters.DateTimeFilter(
        field_name="created_at", method="my_created_day_filter"
    )
    created_while = django_filters.BaseInFilter(
        field_name="created_while", method="my_created_while_filter"
    )

    def my_created_day_filter(self, queryset, name, value):
        return queryset.filter(
            created_at__date__year=value.year,
            created_at__date__month=value.month,
            created_at__date__day=value.day,
        )
    
    def my_created_while_filter(self, queryset, name, value):
        return queryset.filter(
            created_while__in=value
        )


class EnumChoiceField(forms.CharField):
    def __init__(self, *args, choices=None, type=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.__choices = choices
        self.__type = type

    @property
    def overwritten_type(self):
        if self.__type:
            return self.__type
        return InputEnum.from_choices(self.__choices)


@convert_form_field.register(EnumChoiceField)
def convert_form_field_to_string_list(field):
    return field.overwritten_type(required=field.required)


class EnumFilter(django_filters.CharFilter):
    field_class = EnumChoiceField

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert self.field_name

    def filter(self, qs, value):
        """Convert the filter value to a primary key before filtering"""
        if value:
            return qs.filter(**{self.field_name: value})
        return qs


class LinkableModelsFilter(EnumFilter):
    def filter(self, qs, value):
        """Convert the filter value to a primary key before filtering"""
        if value:
            ct = ContentType.objects.get_for_model(linkable_models[value])
            print(ct, self.field_name)
            return qs.filter(**{self.field_name: ct})
        return qs


class StageFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )


class ObjectiveFilter(
    PinnedFilterMixin, AppFilterMixin, IdsFilter, django_filters.FilterSet
):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    search = django_filters.CharFilter(
        method="search_filter", label="The substring you want to filter by"
    )

    def search_filter(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) | Q(instruments__name__icontains=value)
        )


class PositionFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    stage = django_filters.ModelChoiceFilter(
        field_name="stage", queryset=Stage.objects.all()
    )
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )


class DatasetFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )


class OmeroFilter(TimeFilterMixin, IdsFilter, django_filters.FilterSet):
    order = django_filters.OrderingFilter(
        fields={"acquisition_date": "acquired"},
        label="Order the omeros: options are -acquired or acquired",
    )


class ExperimentFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    order = django_filters.OrderingFilter(fields={"created_at": "time"})
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )


class ContextFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    order = django_filters.OrderingFilter(fields={"created_at": "time"})
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )


class DimensionMapFilter(
    IdsFilter,
    django_filters.FilterSet,
):  
    name = django_filters.CharFilter(
        field_name="omero__representation__name", lookup_expr="icontains", label="Search for substring of name"
    )
    dims = MultiEnumFilter(type=Dimension, field_name="dimension")
    index = django_filters.NumberFilter(field_name="index")


class ViewFilter(
    IdsFilter,
    django_filters.FilterSet,
):  
    name = django_filters.CharFilter(
        field_name="omero__representation__name", lookup_expr="icontains", label="Search for substring of name"
    )
    omero = django_filters.ModelChoiceFilter(
        field_name="omero", queryset=Omero.objects.all()
    )
    representation = django_filters.ModelChoiceFilter(
        field_name="omero__representation", queryset=Representation.objects.all()
    )
    z = django_filters.CharFilter(method="z_filter", label="The z you want to filter by either interger or slice string")
    active_for_z = django_filters.NumberFilter(method="active_z_filter", label="The z you want to filter by either interger or slice string")
    active_for_t = django_filters.NumberFilter(method="active_t_filter", label="The z you want to filter by either interger or slice string")
    active_for_x = django_filters.NumberFilter(method="active_x_filter", label="The z you want to filter by either interger or slice string")
    active_for_y = django_filters.NumberFilter(method="active_y_filter", label="The z you want to filter by either interger or slice string")
    active_for_c = django_filters.NumberFilter(method="active_c_filter", label="The z you want to filter by either interger or slice string")



    def z_filter(self, queryset, name, value):
        if ":" in value:
            zmin, zmax = value.split(":")
            if zmin == "":
                zmin = None
            else:
                zmin = int(zmin)
            if zmax == "":
                zmax = None
            else:
                zmax = int(zmax)

            if zmin and zmax:
                return queryset.filter(z_min__gte=zmin, z_max__lte=zmax)
            elif zmin:
                return queryset.filter(z_min__gte=zmin)
            elif zmax:
                return queryset.filter(z_max__lte=zmax)
            else:
                return queryset

        else:
            value = int(value)
            return queryset.filter(z_min=value, z_max=value)
        
    def active_z_filter(self, queryset, name, value):
        value = int(value)
        return queryset.filter( (Q(z_max=None) | Q(z_max__lte=value)) & (Q(z_min__gte=value)  | Q(z_min=None)) )
    
    def active_y_filter(self, queryset, name, value):
        value = int(value)
        return queryset.filter( (Q(y_max=None) | Q(y_max__lte=value)) & (Q(y_min__gte=value)  | Q(y_min=None)) )

    def active_x_filter(self, queryset, name, value):
        value = int(value)
        return queryset.filter( (Q(x_max=None) | Q(x_max__lte=value)) & (Q(x_min__gte=value)  | Q(x_min=None)) )

    def active_c_filter(self, queryset, name, value):
        value = int(value)
        return queryset.filter( (Q(c_max=None) | Q(c_max__lte=value)) & (Q(c_min__gte=value)  | Q(c_min=None)) )
    
    def active_t_filter(self, queryset, name, value):
        value = int(value)
        return queryset.filter( (Q(t_max=None) | Q(t_max__lte=value)) & (Q(t_min__gte=value)  | Q(t_min=None)) )

    



class RelationFilter(IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )

class MetasFilter(IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )

class ChannelFilter(IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )


class EraFilter(IdsFilter, TimeFilterMixin,django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )

class TimepointFilter(IdsFilter, TimeFilterMixin, django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    delta_t = django_filters.NumberFilter(field_name="delta_t")
    order = django_filters.OrderingFilter(fields={"delta_t": "delta_t"})


class Filter(IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )

class DataLinkFilter(TimeFilterMixin, IdsFilter, django_filters.FilterSet):
    relation = django_filters.CharFilter(
        field_name="relation__name",
        lookup_expr="iexact",
        label="Search for relationship of name",
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    context = django_filters.ModelChoiceFilter(
        field_name="context", queryset=Context.objects.all()
    )
    order = django_filters.OrderingFilter(fields={"created_at": "time"})

    x_type = LinkableModelsFilter(type=LinkableModels, field_name="x_content_type")
    y_type = LinkableModelsFilter(type=LinkableModels, field_name="y_content_type")


class ThumbnailFilter(
    AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet
):
    name = django_filters.CharFilter(
        field_name="representation__name",
        lookup_expr="icontains",
        label="Search for substring of name",
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )

class VideoFilter(
    AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet
):
    name = django_filters.CharFilter(
        field_name="representation__name",
        lookup_expr="icontains",
        label="Search for substring of name",
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )


class ROIFilter(
    PinnedFilterMixin,
    AppFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    representation = django_filters.ModelChoiceFilter(
        queryset=Representation.objects.all(), field_name="representation"
    )
    repname = django_filters.CharFilter(field_name="representation__name")
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    ordering = django_filters.OrderingFilter(fields={"created_at": "time"})
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )
    type = MultiEnumFilter(type=RoiTypeInput, field_name="type")


class LabelFilter(AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet):
    representation = django_filters.ModelChoiceFilter(
        queryset=Representation.objects.all(), field_name="representation"
    )
    creator = django_filters.NumberFilter(field_name="creator")
    name = django_filters.CharFilter(
        field_name="name",
        lookup_expr="icontains",
        label="Search for substring of name",
    )


class ModelFilter(AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet):
    creator = django_filters.NumberFilter(field_name="creator")
    name = django_filters.CharFilter(
        field_name="name",
        lookup_expr="icontains",
        label="Search for substring of name",
    )
    contexts = django_filters.ModelChoiceFilter(
        queryset=Context.objects.all(), field_name="contexts"
    )


class FeatureFilter(
    AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet
):
    label = django_filters.ModelChoiceFilter(
        queryset=Label.objects.all(),
        field_name="label",
        label="The corresponding label that you want to filter by",
    )
    creator = django_filters.NumberFilter(field_name="creator")
    keys = django_filters.BaseInFilter(
        method="my_key_filter", label="The key you want to filter by"
    )
    substring = django_filters.CharFilter(
        method="my_substring_filter", label="The substring you want to filter by"
    )

    def my_key_filter(self, queryset, name, value):
        return queryset.filter(key__in=value)

    def my_substring_filter(self, queryset, name, value):
        return queryset.filter(key__contains=value)


class RepresentationFilter(
    AppFilterMixin,
    PinnedFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    tags = django_filters.BaseInFilter(
        method="my_tag_filter", label="The tags you want to filter by"
    )
    experiments = django_filters.ModelMultipleChoiceFilter(
        queryset=Experiment.objects.all(),
        field_name="sample__experiments",
        label="The Experiment the Sample of this Representation belongs to",
    )
    stages = django_filters.ModelMultipleChoiceFilter(
        queryset=Stage.objects.all(),
        field_name="omero__position__stage",
        label="The Stage this Representation belongs to",
    )
    samples = django_filters.ModelMultipleChoiceFilter(
        queryset=Sample.objects.all(), field_name="sample"
    )
    no_children = django_filters.BooleanFilter(
        method="no_children_filter", label="Only show Representations without children"
    )
    ordering = django_filters.OrderingFilter(fields={"created_at": "time"})
    has_metric = django_filters.CharFilter(
        method="metric_filter", label="Filter by required Metric Keys (seperated by ,)"
    )
    order = django_filters.BaseInFilter(method="order_filter", label="Order by Keys")
    variety = EnumFilter(type=RepresentationVarietyInput, field_name="variety")
    forceThumbnail = django_filters.BooleanFilter(
        field_name="thumbnail", method="thumbnail_filter"
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    derived_tags = django_filters.BaseInFilter(
        method="derived_tag_filter", label="The tags you want to filter by"
    )

    class Meta:
        model = Representation
        fields = ["name"]

    def my_tag_filter(self, queryset, name, value):
        if value:
            return queryset.filter(tags__name__in=value)
        return queryset

    def my_ids_filter(self, queryset, name, value):
        if value:
            return queryset.filter(id__in=value)
        return queryset

    def derived_tag_filter(self, queryset, name, value):
        return queryset.filter(derived__tags__name__in=value).distinct()

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)

    def thumbnail_filter(self, queryset, name, value):
        if value:
            return queryset.exclude(thumbnail__isnull=True).exclude(thumbnail__exact="")
        return queryset

    def metric_filter(self, queryset, name, value):
        tag_list = [item.strip() for item in value.split(",")]

        return queryset.filter(metrics__key__in=tag_list)

    def no_children_filter(self, queryset, name, value):
        return queryset.filter(origins=None) if value else queryset


class MetricFilter(
    AppFilterMixin, TimeFilterMixin, IdsFilter, django_filters.FilterSet
):
    keys = django_filters.BaseInFilter(
        method="my_key_filter", label="The key you want to filter by"
    )
    sample = django_filters.ModelChoiceFilter(
        queryset=Sample.objects.all(), field_name="sample"
    )
    experiment = django_filters.ModelChoiceFilter(
        queryset=Sample.objects.all(), field_name="experiment"
    )
    representation = django_filters.ModelChoiceFilter(
        queryset=Representation.objects.all(), field_name="rep"
    )
    order = django_filters.BaseInFilter(method="order_filter", label="Order by Keys")
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )

    def my_key_filter(self, queryset, name, value):
        return queryset.filter(key__in=value)

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)


class SampleFilter(
    AppFilterMixin,
    PinnedFilterMixin,
    TimeFilterMixin,
    IdsFilter,
    django_filters.FilterSet,
):
    experiments = django_filters.ModelMultipleChoiceFilter(
        queryset=Experiment.objects.all(), field_name="experiments"
    )
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    bioseries = django_filters.CharFilter(
        field_name="bioseries__name", label="The name of the desired BioSeries"
    )
    ids = IDChoiceFilter(
        label="The ids you want to filter by",
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    representations = django_filters.ModelMultipleChoiceFilter(
        queryset=Representation.objects.all(),
        field_name="representations",
        label="The ids you want to filter by",
    )
    order = django_filters.BaseInFilter(method="order_filter", label="Order by Keys")
    tags = django_filters.BaseInFilter(
        label="The tags you want to filter by", field_name="tags__name"
    )

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)

    def my_ids_filter(self, queryset, name, value):
        if value:
            return queryset.filter(id__in=value)
        return queryset

    def my_repids_filter(self, queryset, name, value):
        print(value)
        if value:
            return queryset.filter(representations__id__in=value)
        return queryset

    class Meta:
        model = Sample
        fields = ["creator", "experiments", "bioseries", "name"]


class TagFilter(IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)

    class Meta:
        model = Tag
        fields = ["name"]


class OmeroFileFilter(TimeFilterMixin, IdsFilter, django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)

    class Meta:
        model = OmeroFile
        fields = ["name"]


class InstrumentFilter(
    TimeFilterMixin, AppFilterMixin, IdsFilter, django_filters.FilterSet
):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Instrument
        fields = ["name"]
