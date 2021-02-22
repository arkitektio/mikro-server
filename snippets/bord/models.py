# import the logging library
from .settings import get_active_settings
import logging
import uuid
from django.db import models

from .fields import ParquetFileField
from .managers import BordManager, DelayedBordManager

# Get an instance of a logger
logger = logging.getLogger(__name__)


active_setting = get_active_settings()

class BordBase(models.Model):

    parquet = ParquetFileField(verbose_name="store",storage=active_setting.getStorageClass(), upload_to="parquet", blank=True, null= True, help_text="The location of the Parquet on the Storage System (S3 or Media-URL)")
    name = models.CharField(max_length=1000, blank=True, null= True,help_text="Cleartext name")
    signature = models.CharField(max_length=300,null=True, blank=True,help_text="The Dataframes unique signature")
    unique = models.UUIDField(default=uuid.uuid4, editable=False)

    objects = BordManager()
    delayed = DelayedBordManager()

    class Meta:
        abstract = True
        
    @property
    def bord(self):
        """Accessor for the dask.Dataframe attached to the Model
        
        Raises:
            NotImplementedError: If Array does not contain a Parquet
        
        Returns:
            [dask.Dataframe] -- The dask.Dataframe
        """
        if self.parquet:
            array = self.parquet.load()
            return array
        else:
            raise NotImplementedError("This array does not have a parquet")



    def _repr_html_(self):
        return "<h1>" + f'Bord: {str(self.name)} in {self.parquet.url}' + "</h1>"




class Bord(BordBase):

    class Meta:
        abstract = True
