from django.contrib.auth import get_user_model
from django.db import models
from django.utils.module_loading import import_string
from bord.fields import ParquetField
from django.conf import settings
from taggit.managers import TaggableManager
from grunnlag.models import Representation, Sample, Experiment
import uuid


class Table(models.Model):
    representation = models.ForeignKey(
        Representation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tables",
    )
    sample = models.ForeignKey(
        Sample, on_delete=models.CASCADE, null=True, blank=True, related_name="tables"
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tables",
    )
    name = models.CharField(max_length=2000)
    columns = models.JSONField(default=list)
    store = ParquetField(
        verbose_name="store",
        storage=import_string(settings.BORD["STORAGE_CLASS"]),
        upload_to="parquet",
        blank=True,
        null=True,
        help_text="The location of the Parquet on the Storage System (S3 or Media-URL)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True
    )
    tags = TaggableManager()

    def save(self, *args, **kwargs):

        # Initial Model pure save calls will not have an Array and raise Exception
        # Update calls (also through from_xarray) will already have an array that needs to be updated
        if not self.store.name:
            path = f"table-{uuid.uuid4()}.parquet"
            self.store.name = path

        return super().save(*args, **kwargs)
