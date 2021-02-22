from django.conf import settings
from django.contrib.postgres.fields.array import ArrayField
from django.db import models
from .stores import XArrayStore

class StoreField(models.FileField):
    attr_class = XArrayStore
    description = "XArrayStore"


class ShapeField(ArrayField):

    def __init__(self, **kwargs) -> None:
        print(self)
        super().__init__(models.IntegerField(), **kwargs)


class DimsField(ArrayField):
    
    def __init__(self, **kwargs) -> None:
        super().__init__(models.CharField(max_length=100), **kwargs)
