from django.db import models

from bord.store import FieldParquet


class ParquetField(models.FileField):
    attr_class = FieldParquet
    description = "FieldParquet"
