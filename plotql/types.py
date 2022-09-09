from django.contrib.auth import get_user_model
from balder.types.object import BalderObject
from plotql import models
from django.contrib.auth.models import Group as GroupModel
from balder.registry import register_type
from graphene.types.generic import GenericScalar


class Plot(BalderObject):
    class Meta:
        model = models.Plot
