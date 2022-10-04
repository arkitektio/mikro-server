from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from perms.filters import UserFilter
from perms import types, models


class Users(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = types.User
        filter = UserFilter
        operation = "users"