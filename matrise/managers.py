# import the logging library
import io
import json
import logging
import numpy as np
import xarray as xr
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db.models.manager import Manager
from .settings import get_active_settings

logger = logging.getLogger(__name__)


class MatriseManager(Manager):

    def from_xarray(self, array: xr.DataArray, fileversion=get_active_settings().FILE_VERSION, apiversion= get_active_settings().API_VERSION,**kwargs ):
        """Takes an DataArray and the model arguments and returns the created Model
        
        Arguments:
            array {xr.DataArray} -- An xr.DataArray as a LarvikArray
        
        Returns:
            [models.Model] -- [The Model]
        """

        item = self.model(**kwargs)
        item.save() # Important. we now assign a store to this

        item.store.save(array, compute=True, fileversion=fileversion, apiversion= apiversion)
        
        item.save()
        return item
