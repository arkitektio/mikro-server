from enum import Enum
from typing import Type
from django.db.models.enums import TextChoices
import graphene

class InputEnum:

    @staticmethod
    def from_choices(enum_class: Type[TextChoices], description=None):
        enum_class.__name__ = f"{enum_class.__name__}Input"
        description = description or enum_class.__doc__

        return graphene.Enum.from_enum(enum_class, description= lambda v: enum_class[v].label if v is not None else description)

