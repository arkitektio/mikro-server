import django_filters


class UserFilter(django_filters.FilterSet):
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
