from grunnlag.filters import TimeFilterMixin
import django_filters


class PlotFilter(TimeFilterMixin, django_filters.FilterSet):
    pass