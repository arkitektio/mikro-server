from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group as GroupModel
from balder.types.object import BalderObject
import graphene
from django.contrib.auth.models import Permission


class Permission(BalderObject):
    unique = graphene.String(description="Unique ID for this permission", required=True)

    def resolve_unique(root, info):
        return f"{root.content_type.app_label}.{root.codename}"

    class Meta:
        model = Permission


class User(BalderObject):
    color = graphene.String(description="The color of the user", required=True)

    def resolve_color(root, info):
        return "#ff00ff"

    class Meta:
        model = get_user_model()
        fields = [
            "id",
            "username",
            "email",
            "last_name",
            "first_name",
            "groups",
        ]


class Group(BalderObject):
    class Meta:
        model = GroupModel
