from django.contrib.auth import get_user_model
from django.db import models
from django.utils.module_loading import import_string
from bord.fields import ParquetField
from django.conf import settings
from taggit.managers import TaggableManager
from grunnlag.models import Representation, Sample, Experiment, CreatedThroughMixin, InDatasetMixin
import uuid
from bord.storage import PrivateRenderStorage

class Table(CreatedThroughMixin, InDatasetMixin, models.Model):
    """ A Table is a collection of tabular data.

    It provides a way to store data in a tabular format and associate it with a Representation,
    Sample or Experiment. It is a way to store data that might be to large to store in a
    Feature or Metric on this Experiments. Or it might be data that is not easily represented
    as a Feature or Metric.

    Tables can be easily created from a pandas DataFrame and can be converted to a pandas DataFrame.
    Its columns are defined by the columns of the DataFrame.

    
    """



    rep_origins = models.ManyToManyField(
        Representation,
        related_name="tables",
        help_text="The Representation this Table belongs to",
    )
    sample = models.ForeignKey(
        Sample, on_delete=models.CASCADE, null=True, blank=True, related_name="tables", help_text="Sample this table belongs to"
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tables",help_text="The Experiment this Table belongs to.",
    )
    name = models.CharField(max_length=2000)
    columns = models.JSONField(default=list, help_text="List of column and their properties")
    store = ParquetField(
        verbose_name="store",
        storage=import_string(settings.BORD["STORAGE_CLASS"]),
        upload_to="parquet",
        blank=True,
        null=True,
        help_text="The location of the Parquet on the Storage System (S3 or Media-URL)",
    )
    pinned_by = models.ManyToManyField(get_user_model(), related_name="pinned_tables")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the Table was created")
    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, null=True, blank=True, help_text="The creator of the Table"
    )
    tags = TaggableManager()

    def save(self, *args, **kwargs):

        # Initial Model pure save calls will not have an Array and raise Exception
        # Update calls (also through from_xarray) will already have an array that needs to be updated
        if not self.store.name:
            path = f"table-{uuid.uuid4()}.parquet"
            self.store.name = path

        return super().save(*args, **kwargs)




class Graph(CreatedThroughMixin, InDatasetMixin, models.Model):
    tables = models.ManyToManyField(
        Table, related_name="graphs"
    )
    name = models.CharField(max_length=2000)
    used_columns = models.JSONField(default=list, help_text="List of columns of the Table that are used in this Graph", null=True, blank=True)
    image = models.ImageField(
        upload_to="graphs", null=True, storage=PrivateRenderStorage()
    )
    tags = TaggableManager()
    pinned_by = models.ManyToManyField(get_user_model(), related_name="pinned_graphs")













from . import signals
