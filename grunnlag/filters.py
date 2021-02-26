import django_filters
from .models import Experiment, Representation, Sample


class ExperimentFilter(django_filters.FilterSet):
    creator = django_filters.NumberFilter(field_name='creator')

class RepresentationFilter(django_filters.FilterSet):
    tags = django_filters.CharFilter(method='my_tag_filter',label="The tags you want to filter by")
    experiment = django_filters.ModelChoiceFilter(queryset=Experiment.objects.all(), field_name= "sample__experiment", label="The Experiment the Sample of this Representation belongs to")
    sample = django_filters.ModelChoiceFilter(queryset=Sample.objects.all(),field_name= "sample")
    ordering = django_filters.OrderingFilter(fields={"created_at":"time"})

    class Meta:
        model = Representation
        fields = ["name","variety"]

    def my_tag_filter(self, queryset, name, value):
        tag_list = [item.strip() for item in value.split(",")]

        return queryset.filter(tags__name__in=tag_list)


class SampleFilter(django_filters.FilterSet):

    experiment = django_filters.CharFilter(field_name= "experiment__name")
    bioseries = django_filters.CharFilter(field_name="bioseries__name",  label="The name of the desired BioSeries")

    class Meta:
        model = Sample
        fields = ["creator","experiment","bioseries"]