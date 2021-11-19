import django_filters
from bord.models import Table


class TableFilter(django_filters.FilterSet):
    tags = django_filters.BaseInFilter(
        method="my_tag_filter", label="The tags you want to filter by"
    )
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )

    class Meta:
        model = Table
        fields = ["name"]

    def my_tag_filter(self, queryset, name, value):
        return queryset.filter(tags__name__in=value)

    def order_filter(self, queryset, name, value):
        return queryset.order_by(*value)

    def thumbnail_filter(self, queryset, name, value):
        if value:
            return queryset.exclude(thumbnail__isnull=True).exclude(thumbnail__exact="")
        return queryset

    def metric_filter(self, queryset, name, value):
        tag_list = [item.strip() for item in value.split(",")]

        return queryset.filter(metrics__key__in=tag_list)
