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
from guardian.shortcuts import assign_perm
import logging


class InputVector(graphene.InputObjectType):
    x = graphene.Float(description="X-coordinate")
    y = graphene.Float(description="Y-coordinate")
    z = graphene.Float(description="Z-coordinate")


class ChangePermissionsResult(graphene.ObjectType):
    success = graphene.Boolean()
    message = graphene.String()


class ChangePermissions(BalderMutation):
    """Creates a Sample"""

    class Arguments:
        type = graphene.Argument(AvailableModelsEnum, required=True)
        object = graphene.ID(
            required=True, description="The Representationss this sROI belongs to"
        )
        users = graphene.List(graphene.String, required=False)
        groups = graphene.List(graphene.String, required=False)
        permissions = graphene.List(graphene.String, required=True)

    @bounced(anonymous=False)
    def mutate(root, info, type, object, permissions, users=[], groups=[]):

        model_class = ct_types[type].model_class()
        instance = model_class.objects.get(id=object)

        for permission in permissions:
            for user in users:
                logging.warning(f"Assigning {permission} to {user}")
                assign_perm(permission, get_user_model().objects.get(id=user), instance)

            for group in groups:
                logging.warning(f"Assigning {permission} to {group}")
                assign_perm(permission, Group.objects.get(name=group), instance)

        return {"success": True}

    class Meta:
        type = ChangePermissionsResult
        operation = "changePermissions"
