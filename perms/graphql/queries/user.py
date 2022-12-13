from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from perms.filters import UserFilter
from perms import types, models
from django.contrib.auth import get_user_model

class Users(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = types.User
        filter = UserFilter
        operation = "users"



class User(BalderQuery):
    """Get a list of users"""
    class Arguments:
        id = graphene.ID(description="Unique app name for user")

    def resolve(root, info, id):
        return get_user_model().objects.get(id=id)

    class Meta:
        list = False
        type = types.User
        operation = "user"
