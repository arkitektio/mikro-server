import django_filters


class CommentFilter(django_filters.FilterSet):
    username = django_filters.CharFilter(
        field_name="username",
        lookup_expr="icontains",
        label="Search for substring of name",
    )
    email = django_filters.CharFilter(
        field_name="email",
        lookup_expr="icontains",
        label="Search for substring of name",
    )
    search = django_filters.CharFilter(
        field_name="first_name",
        lookup_expr="icontains",
        label="Search for substring of name",
    )

    flat = django_filters.BooleanFilter(
        method="flat_filter",
        label="Flat",
    )

    def flat_filter(self, queryset, name, value):
        if value:
            return queryset.filter(parent=None)
        return queryset
