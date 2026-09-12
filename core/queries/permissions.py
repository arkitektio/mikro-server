from core import models, types
import strawberry
from typing import List, Optional
from django.contrib.contenttypes.models import ContentType
from guardian.models import UserObjectPermission 
from django.contrib.auth.models import Permission
from kante.types import Info


#: Structure identifiers clients pass in, mapped to the model they name. These are *values*,
#: not schema names, so no schema diff warns anyone when one changes -- which is why the
#: retired spellings stay here as aliases rather than being deleted outright.
identifier_model_map = {
    "@mikro/arraydataset": models.ArrayDataset,
    "@mikro/folder": models.Folder,
    # Back-compat alias: `Dataset` was renamed to `Folder`, but this identifier is a value
    # clients pass in rather than a schema name, so no schema diff would warn them.
    "@mikro/dataset": models.Folder,
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
    

    content_type = ContentType.objects.get_for_model(model)
    user_permissions = UserObjectPermission.objects.filter(object_pk=object, content_type=content_type, user__sub__isnull=False).all()
        
    return [types.UserObjectPermission(user=user_permissions.user, permission=user_permissions.permission.codename) for user_permissions in user_permissions]



@strawberry.type
class PermissionOption:
    value: strawberry.ID  # the Permission ID
    label: str            # human-readable name


def available_permissions(
    identifier: str,
    search: Optional[str] = None,
    values: Optional[List[strawberry.ID]] = None,
) -> List[PermissionOption]:
    model = identifier_model_map.get(identifier)
    if model is None:
        raise ValueError(f"Unknown identifier: {identifier}")

    content_type = ContentType.objects.get_for_model(model)
    qs = Permission.objects.filter(content_type=content_type)

    if values:
        qs = qs.filter(id__in=values)
    elif search:
        qs = qs.filter(name__icontains=search)

    return [
        PermissionOption(value=str(p.id), label=p.name)
        for p in qs
    ]