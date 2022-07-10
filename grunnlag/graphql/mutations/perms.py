from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from graphene.types.generic import GenericScalar
from lok import bounced
from balder.types import BalderMutation
import graphene
from grunnlag.enums import RoiTypeInput
from grunnlag import models, types
from enum import Enum
from grunnlag.graphql.utils import AvailableModelsEnum, ct_types
from guardian.shortcuts import (
    assign_perm,
    get_users_with_perms,
    get_groups_with_perms,
    remove_perm,
)
import logging


class InputVector(graphene.InputObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")


class ChangePermissionsResult(graphene.ObjectType):
    success = graphene.Boolean()
    message = graphene.String()


class UserAssignmentInput(graphene.InputObjectType):
    permissions = graphene.List(graphene.String, required=True)
    user = graphene.String(required=True, description="The user email")


class GroupAssignmentInput(graphene.InputObjectType):
    permissions = graphene.List(graphene.String, required=True)
    group = graphene.ID(required=True)


class ChangePermissions(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        type = graphene.Argument(AvailableModelsEnum, required=True)
        object = graphene.ID(
            required=True, description="The Representationss this sROI belongs to"
        )
        userAssignments = graphene.List(UserAssignmentInput)
        groupAssignments = graphene.List(GroupAssignmentInput)

    @bounced(anonymous=False)
    def mutate(root, info, type, object, userAssignments=[], groupAssignments=[]):

        model_class = ct_types[type].model_class()
        instance = model_class.objects.get(id=object)

        users = get_users_with_perms(
            instance, attach_perms=True, with_group_users=False, with_superusers=False
        )
        groups = get_groups_with_perms(instance, attach_perms=True)

        # remove all permissions
        for user, permissions in users.items():
            for permission in permissions:
                remove_perm(permission, user, instance)

        for group, permissions in groups.items():
            for permission in permissions:
                remove_perm(permission, group, instance)

        for ass in userAssignments:
            logging.warning(f"Assigning {ass['permissions']} to {ass['user']}")
            for perm in ass["permissions"]:
                assign_perm(
                    perm,
                    get_user_model().objects.get(email=ass["user"]),
                    instance,
                )

        for ass in groupAssignments:
            logging.warning(f"Assigning {ass['permissions']} to {ass['group']}")
            for perm in ass["permissions"]:
                assign_perm(
                    perm,
                    Group.objects.get(name=ass["group"]),
                    instance,
                )

        return {"success": True}

    class Meta:
        type = ChangePermissionsResult
        operation = "changePermissions"
