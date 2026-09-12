import strawberry
import strawberry_django


def build_prescoped_queryset(info, queryset):
    # Reads are always scoped to the request's organization; there is no
    # per-request scope override.
    return queryset.filter(organization=info.context.request.organization)


def apply_link_filters(queryset, filters_input, info) -> list:  # noqa: ANN001 - a QuerySet, a strawberry filter input, kante's Info
    """Apply an optional ``FileLinkFilter`` to a link queryset and evaluate it.

    Lives here because five resolvers across four modules need it -- ``sourceFiles`` and
    ``exports`` on each container, plus ``derivedContainers`` and ``exportedFrom`` on ``File``.

    It is also the seam that publishes ``FileLinkFilter`` into the SDL at all. Declaring the
    filter on the django_type is not enough: nothing referenced it, so it was silently absent
    from the schema. A filter type reaches the SDL only by being some field's argument.
    """
    if filters_input is not strawberry.UNSET and filters_input is not None:
        queryset = strawberry_django.filters.apply(filters_input, queryset, info)
    return list(queryset)
