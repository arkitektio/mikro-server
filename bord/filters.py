import django_filters
from bord.models import Table
from django.contrib.auth import get_user_model


class TableFilter(django_filters.FilterSet):
    tags = django_filters.BaseInFilter(
        method="my_tag_filter", label="The tags you want to filter by"
    )
    name = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of name"
    )
    created_after = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr=("gt"),
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at", lookup_expr=("lt")
    )
    creator = django_filters.ModelChoiceFilter(
        field_name="creator", queryset=get_user_model().objects.all()
    )
    pinned = django_filters.BooleanFilter(
        method="my_pinned_filter", label="Filter by pinned"
    )
    created_day = django_filters.DateTimeFilter(
        field_name="created_at", method="my_created_day_filter"


    )

    def my_created_day_filter(self, queryset, name, value):
        return queryset.filter(created_at__date__year=value.year, created_at__date__month=value.month, created_at__date__day=value.day)


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
