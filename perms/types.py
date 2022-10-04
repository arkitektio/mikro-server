from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group as GroupModel
from balder.types.object import BalderObject
import graphene
from django.contrib.auth.models import Permission


class Permission(BalderObject):
    """ A Permission object

    This object represents a permission in the system. Permissions are
    used to control access to different parts of the system. Permissions
    are assigned to groups and users. A user has access to a part of the
    system if the user is a member of a group that has the permission
    assigned to it.
    """
    unique = graphene.String(description="Unique ID for this permission", required=True)

    def resolve_unique(root, info):
        return f"{root.content_type.app_label}.{root.codename}"

    class Meta:
        model = Permission


class User(BalderObject):
    """User
    
    This object represents a user in the system. Users are used to
    control access to different parts of the system. Users are assigned 
    to groups. A user has access to a part of the system if the user is
    a member of a group that has the permission assigned to it.

    Users can be be "creator" of objects. This means that the user has
    created the object. This is used to control access to objects. A user
    can only access objects that they have created, or objects that they
    have access to through a group that they are a member of.

    See the documentation for "Object Level Permissions" for more information.
    
    """
    color = graphene.String(description="The prefered color of the user", required=True)

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
    """Group
    
    This object represents a group in the system. Groups are used to
    control access to different parts of the system. Groups are assigned
    to users. A user has access to a part of the system if the user is
    a member of a group that has the permission assigned to it.

    
    
    """
    class Meta:
        model = GroupModel
