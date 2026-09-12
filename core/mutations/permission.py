from core import models, types
from core.scoping import get_for_org
from kante.types import Info
from guardian.shortcuts import assign_perm
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Permission
from guardian.models import UserObjectPermission
import kante
import strawberry
from strawberry import ID
from pydantic import BaseModel, Field

User = get_user_model()

identifier_model_map = {
    "@mikro/arraydataset": models.ArrayDataset,
    "@mikro/folder": models.Folder,
    # Back-compat alias: `Dataset` was renamed to `Folder`, but this identifier is a value
    # clients pass in rather than a schema name, so no schema diff would warn them.
    "@mikro/dataset": models.Folder,
    "@mikro/file": models.File,
}


class AssignUserPermissionInputModel(BaseModel):
    identifier: str = Field(description='The type identifier of the object, e.g. "@mikro/image"')
    object: str = Field(description="The primary key of the object to assign permissions on")
    user: str = Field(description="The primary key of the user to assign permissions to")
    permissions: list[str] = Field(description='The permissions to assign, e.g. ["view_image", "change_image"]')


@kante.pydantic_input(AssignUserPermissionInputModel, description="Input for assigning object-level permissions to a user")
class AssignUserPermissionInput:
    """Input for assigning object-level permissions to a user"""

    identifier: str = strawberry.field(description='The type identifier of the object, e.g. "@mikro/image"')
    object: ID = strawberry.field(description="The primary key of the object to assign permissions on")
    user: ID = strawberry.field(description="The primary key of the user to assign permissions to")
    permissions: list[str] = strawberry.field(description='The permissions to assign, e.g. ["view_image", "change_image"]')


def assign_user_permission(
    info: Info,
    input: AssignUserPermissionInput,
) -> list[types.UserObjectPermission]:
    parsed = input.to_pydantic()
    # Resolve the model
    model = identifier_model_map.get(parsed.identifier)
    if model is None:
        raise ValueError(f"Unknown identifier: {parsed.identifier}")

    # Get the object and user
    obj = get_for_org(model, info, pk=parsed.object)
    user = User.objects.get(sub=parsed.user)

    # Assign each permission
    for perm in parsed.permissions:
        x = Permission.objects.get(id=perm)
        assign_perm(x.codename, user, obj)

    # Return all permissions for this object and user
    content_type = ContentType.objects.get_for_model(model)

    user_permissions = UserObjectPermission.objects.filter(
        object_pk=parsed.object,
        content_type=content_type, user__sub__isnull=False
    ).all()

    return [types.UserObjectPermission(user=user_permissions.user, permission=user_permissions.permission.codename) for user_permissions in user_permissions]
