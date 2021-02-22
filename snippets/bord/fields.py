from django.conf import settings
from django.db import models

from bord.stores import ParquetStore


class ParquetFileField(models.FileField):
    attr_class = ParquetStore
    description = "ParquetStore"
