import strawberry
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from guardian.models import UserObjectPermission
from kante.types import Info

from core import models, types
from core.scoping import get_for_org

#: The structures whose object permissions can be listed and granted, by the identifier clients
#: pass in. The identifiers are the ones mikro declares it hosts (``mikro_server.service``).
identifier_model_map = {
    "@mikro/arraydataset": models.ArrayDataset,
    "@mikro/folder": models.Folder,
    "@mikro/file": models.File,
}


def permissions(
    info: Info,
    identifier: str,
    object: strawberry.ID,
) -> list[types.UserObjectPermission]:

    model = identifier_model_map.get(identifier)
    if model is None:
        raise ValueError(f"Unknown identifier: {identifier}")

    # The grants name users of the object's organization; only that organization may list them.
    get_for_org(model, info, pk=object)

    content_type = ContentType.objects.get_for_model(model)
    user_permissions = UserObjectPermission.objects.filter(
        object_pk=object, content_type=content_type, user__sub__isnull=False
    ).all()

    return [
        types.UserObjectPermission(
            user=user_permissions.user, permission=user_permissions.permission.codename
        )
        for user_permissions in user_permissions
    ]


@strawberry.type
class PermissionOption:
    value: strawberry.ID  # the Permission ID
    label: str  # human-readable name


def available_permissions(
    identifier: str,
    search: str | None = None,
    values: list[strawberry.ID] | None = None,
) -> list[PermissionOption]:
    model = identifier_model_map.get(identifier)
    if model is None:
        raise ValueError(f"Unknown identifier: {identifier}")

    content_type = ContentType.objects.get_for_model(model)
    qs = Permission.objects.filter(content_type=content_type)

    if values:
        qs = qs.filter(id__in=values)
    elif search:
        qs = qs.filter(name__icontains=search)

    return [PermissionOption(value=str(p.id), label=p.name) for p in qs]
