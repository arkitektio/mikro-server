import strawberry
import strawberry_django
from strawberry.scalars import JSON

from core.scoping import scope_queryset


def build_prescoped_queryset(info, queryset):
    """Limit ``queryset`` to the request's organization.

    Every type backing a Query field must route its ``get_queryset`` through here:
    strawberry_django runs it for single ``x: T = field()`` fetches as well as lists, and
    nothing else in the stack scopes them. Follows ``core.scoping.organization_path``, so a
    model whose organization sits behind a required FK (a data array, a transformation) is
    scoped the same way as one that carries the column itself.
    """
    return scope_queryset(queryset, info)


class OrgScoped:
    """Mixin that scopes a type's reads to the request's organization (as elektro's does).

    strawberry_django runs ``get_queryset`` for top-level list fields, single ``x: T = field()``
    fetches and nested relations alike, so listing this as a base class tenant-scopes every
    read of the type. Resolved via MRO.
    """

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        return build_prescoped_queryset(info, queryset)


class OrgScopedOrNested(OrgScoped):
    """Tenant scoping for a type that is *also* read as a relation of an already-scoped row.

    A filter on a related manager's queryset throws its prefetch away, so the rows are fetched
    again once per parent. For the children of a wrapper transformation that turned
    ``coordinateGraph`` -- which prefetches them, because a custom resolver's plain list is
    invisible to the optimizer -- into a query per edge.

    Such a row carries no tenancy question of its own *when nested*: it is reached through a
    parent the request already read scoped, and cannot belong to another organization than
    that parent (a child is created with its wrapper). So a queryset that is visibly "the
    relation of a known parent" is returned as it came:

    * ``_result_cache`` filled -- the prefetch already ran;
    * ``_known_related_objects`` set -- Django's mark of a reverse manager bound to one instance.

    Anything else -- a root list, a by-id lookup -- is scoped exactly as :class:`OrgScoped`
    does. Used only where both conditions hold by construction; not a general relaxation.
    """

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        if getattr(queryset, "_result_cache", None) is not None or getattr(queryset, "_known_related_objects", None):
            return queryset
        return build_prescoped_queryset(info, queryset)


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


DESCRIPTORS_DESCRIPTION = (
    "This object's descriptors, a flat mapping of key to value: the facts about it that an action's port can `require` and a trigger can test "
    "(e.g. `@mikro/n_channels`). The keys are the ones mikro declares for this structure, and the values are the ones a signal about the object carries. "
    "Empty for a structure that declares none"
)


def resolve_descriptors(root) -> JSON:  # noqa: ANN001 - the model instance behind any hosted type
    """The descriptors of a hosted object, from its structure's declaration (``mikro_server.service``)."""
    from mikro_server.service import service  # the declaration imports core.models

    return service.describe(root)
