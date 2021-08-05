from enum import Enum
from typing import Type
from django.db.models.enums import TextChoices
import graphene
from graphene_django.registry import get_global_registry

class InputEnum:

    @staticmethod
    def from_choices(enum_class: Type[TextChoices], description=None):
        name = f"{enum_class.__name__}Input"
        description = description or enum_class.__doc__

        def des(v):
            return enum_class[v.name].label if v is not None else description

        return graphene.Enum(name, [(tag.name, tag.value) for tag in enum_class], description= des)

