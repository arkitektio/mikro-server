# import the logging library
from .settings import get_active_settings
import logging
# Get an instance of a logger
from uuid import uuid4

from dask.dataframe import DataFrame
from django.conf import settings
from django.db.models.manager import Manager


logger = logging.getLogger(__name__)

active_settings = get_active_settings()

class DelayedBordManager(Manager):
    generator = active_settings.getPathGeneratorClass()
    group = None
    queryset = None

    def from_dataframe(self, df: DataFrame, fileversion=active_settings.FILE_VERSION, apiversion= active_settings.API_VERSION,**kwargs ):
        """Takes an DataFrame and the model arguments and returns the created Model and the delayed Graph as ZarrStore
        
        Arguments:
            array {xr.DataFrame} -- An xr.DataArray as a LarvikArray
        
        Returns:
            [models.Model] -- The Model
            [xarray.backends.ZarrStore] -- The Delayed Graph as a ParquetStore # TODO Wrong
        """
        item = self.model(**kwargs)
        generated = self.generator.generatePath(item)
        item.parquet.name = generated
            
        # Actually Saving
        item.unique = uuid4()
        graph = item.parquet.save(df, compute=False, fileversion=fileversion, apiversion= apiversion)
        item.save()
        return (item, graph)


class BordManager(Manager):
    generator = active_settings.getPathGeneratorClass()
    group = None
    queryset = None
    
    def get_queryset(self):
        if self.queryset is not None: return self.queryset(self.model, using=self._db)
        return BordQueryset(self.model, using=self._db)


    def from_xarray(self, df: DataFrame, fileversion=active_settings.FILE_VERSION, apiversion= active_settings.API_VERSION,**kwargs ):
        """Takes an DataFrame and the model arguments and returns the created Model
        
        Arguments:
            array {xr.DataArray} -- An xr.DataArray as a LarvikArray
        
        Returns:
            [models.Model] -- [The Model]
        """
        item = self.model(**kwargs)
        generated = self.generator.generatePath(item)
        item.parquet.name = generated

        # Actually Saving
        item.unique = uuid4()
        _ = item.parquet.save(df, compute=False, fileversion=fileversion, apiversion= apiversion)
        item.save()
        return item
