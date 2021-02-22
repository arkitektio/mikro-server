
from django.contrib.postgres.fields.array import ArrayField
from django.db.models.fields import IntegerField
from .settings import get_active_settings
import logging
import uuid
from django.db import models
# Create your models here.
from .fields import DimsField, ShapeField, StoreField
from .managers import  MatriseManager



logger = logging.getLogger(__name__)



active_settings = get_active_settings()

class MatriseBase(models.Model):
    group = "matrise"

    store = StoreField(verbose_name="store",storage=active_settings.getStorageClass(), upload_to="zarr", blank=True, null= True, help_text="The location of the Array on the Storage System (S3 or Media-URL)")
    shape = ArrayField(models.IntegerField(), help_text="The arrays shape", blank=True, null=True)
    dims = ArrayField(models.CharField(max_length=100),help_text="The arrays dimension", blank=True, null=True)
    has_array = models.BooleanField(verbose_name="has_array", help_text="Does this Model have attached Data?", default=False)
    name = models.CharField(max_length=1000, blank=True, null= True,help_text="Cleartext name")
    unique = models.UUIDField(default=uuid.uuid4, editable=False, help_text="A unique identifier for this array")
    fileversion = models.CharField(max_length=1000, help_text="The File Version of this Array", default=active_settings.FILE_VERSION)

    objects = MatriseManager()

    def __init__(self, *args, **kwargs):
        self._array = None
        super().__init__(*args, **kwargs)

    class Meta:
        abstract = True


    def save(self, *args, **kwargs):

        # Initial Model pure save calls will not have an Array and raise Exception
        # Update calls (also through from_xarray) will already have an array that needs to be updated
        try:
            self.shape = list(self.array.shape)
            self.dims = list(self.array.dims)   
            self.has_array = True
        except Exception as e:
            # We are dealing with an initial Creation, lets create a new Store
            if not self.store.name: 
                generated = active_settings.getPathGeneratorClass().generatePath(self)
                self.store.name = generated.path

            self.has_array = False

        return super().save(*args, **kwargs)
        

    @property
    def info(self):
        return self.array.info()


    @property
    def array(self):
        """Accessor for the xr.DataArray class attached to the Model
        
        Raises:
            NotImplementedError: If Array does not contain a Store
        
        Returns:
            [xr.DataArray] -- The xr.DataArray class
        """
        if self._array is not None: return self._array


        if self.store:
            self._array = self.store.loadDataArray()
            return self._array
        else:
            raise NotImplementedError("This represetaion does not have a array")


    @property
    def dataset(self):
        """Accessor for the xr.DataSet class attached to the Model
        
        Raises:
            NotImplementedError: If Array does not contain a Store
        
        Returns:
            [xr.Dataset] -- The Dataset 
        """
        if self.store:
            array = self.store.loadDataset()
            return array
        else:
            raise NotImplementedError("This representation does not have a store/array")

    def _repr_html_(self):
        return "<h1>" + f'Matrise at {str(self.name)} in {self.store}' + "</h1>"



class Matrise(MatriseBase):

    class Meta:
        abstract = True
