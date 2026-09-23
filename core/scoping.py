"""Organization scoping helpers for single-object access.

List fields are tenant-scoped through ``build_prescoped_queryset`` on the
GraphQL types; everything that fetches a single row by ID (mutations,
single-item queries, subscriptions) must go through :func:`for_org`,
:func:`get_for_org` or :func:`aget_for_org` instead of ``Model.objects`` so
one organization cannot read or mutate another organization's rows.

Guardian object-level permissions are assigned (core.signals) but deliberately
not checked on reads; if object-level read enforcement is ever wanted, :func:`for_org` is the
designated seam — every scoped read funnels through it.
"""

from functools import cache

from django.core.exceptions import FieldDoesNotExist
from django.db import models as django_models
from kante.types import Info

# Models that have no organization anywhere in their non-nullable FK graph.
# Access to these is knowingly unscoped until they grow an organization (or a
# required FK to an organization-scoped model). Keep this list short and
# visible — it is the tenancy escape hatch. Currently every model is scoped.
UNSCOPED_MODELS: frozenset[str] = frozenset()

_MAX_PATH_DEPTH = 3


def _find_org_path(model: type[django_models.Model], depth: int) -> str | None:
    try:
        field = model._meta.get_field("organization")
        if field.is_relation:
            return "organization"
    except FieldDoesNotExist:
        pass

    if depth == 0:
        return None

    # Only follow required FKs: a nullable path would silently hide rows
    # whose FK is NULL instead of scoping them.
    for field in model._meta.get_fields():
        if not isinstance(field, django_models.ForeignKey) or field.null:
            continue
        if field.related_model is model:
            continue
        sub_path = _find_org_path(field.related_model, depth - 1)
        if sub_path:
            return f"{field.name}__{sub_path}"

    return None


@cache
def organization_path(model: type[django_models.Model]) -> str | None:
    """Return the ORM lookup path from ``model`` to its organization, if any."""
    return _find_org_path(model, _MAX_PATH_DEPTH)


def scope_queryset(queryset: django_models.QuerySet, info: Info) -> django_models.QuerySet:
    """Narrow an existing ``queryset`` to the request's organization."""
    model = queryset.model
    path = organization_path(model)
    if path is None:
        if model.__name__ not in UNSCOPED_MODELS:
            raise LookupError(
                f"{model.__name__} has no path to an organization and is not "
                "registered in core.scoping.UNSCOPED_MODELS"
            )
        return queryset
    return queryset.filter(**{path: info.context.request.organization})


def for_org(model: type[django_models.Model], info: Info) -> django_models.QuerySet:
    """Return ``model``'s queryset limited to the request's organization."""
    return scope_queryset(model.objects.all(), info)


def get_for_org(model: type[django_models.Model], info: Info, **kwargs) -> django_models.Model:
    """``Model.objects.get`` limited to the request's organization."""
    return for_org(model, info).get(**kwargs)


async def aget_for_org(model: type[django_models.Model], info: Info, **kwargs) -> django_models.Model:
    """Async ``Model.objects.aget`` limited to the request's organization."""
    return await for_org(model, info).aget(**kwargs)
