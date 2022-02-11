import asyncio
from .settings import get_active_settings
import xarray as xr
from xarray.core import dataset
import zarr
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db.models.fields.files import FieldFile
from storages.backends.s3boto3 import S3Boto3Storage
from zarr import blosc
import logging
from asgiref.sync import async_to_sync
import boto3
import s3fs

logger = logging.getLogger(__name__)


# This should be set according to settings
compressor = blosc.Blosc(cname="zstd", clevel=3, shuffle=blosc.Blosc.BITSHUFFLE)
blosc.use_threads = True

zarr.storage.default_compressor = compressor


class NotCompatibleException(Exception):
    pass


class XArrayStore(FieldFile):
    def _getStore(self):

        if isinstance(self.storage, S3Boto3Storage):
            bucket = self.storage.bucket_name
            location = self.storage.location
            s3_path = f"{self.name}"
            # Initilize the S3 file system
            logger.info(f"Bucket [{bucket}]: Connecting to {s3_path}")
            store = s3fs.S3FileSystem(
                client_kwargs={"endpoint_url": get_active_settings().S3_ENDPOINT_URL},
                key=get_active_settings().ACCESS_KEY,
                secret=get_active_settings().SECRET_KEY,
            )
            return store.get_mapper(s3_path)
        if isinstance(self.storage, FileSystemStorage):
            location = self.storage.location
            path = f"{location}/{self.name}"
            # Initilize the S3 file system
            logger.info(f"Folder [{location}]: Connecting to {self.name}")
            store = zarr.DirectoryStore(path)
            return store
        else:
            raise NotImplementedError(
                "Other Storage Formats have not been established yet. Please use S3 like Storage for time being"
            )

    @property
    def connected(self):
        return self._getStore()

    def delete(self):
        if isinstance(self.storage, S3Boto3Storage):
            bucket = self.storage.bucket_name
            store = s3fs.S3FileSystem(
                client_kwargs={"endpoint_url": get_active_settings().S3_ENDPOINT_URL},
                key=get_active_settings().ACCESS_KEY,
                secret=get_active_settings().SECRET_KEY,
            )
            store.rm(f"{self.name}/", recursive=True)
            return
        else:
            raise NotImplementedError(
                "Other Storage Formats have not been established yet. Please use S3 like Storage for time being"
            )

    def save(
        self,
        array: xr.DataArray,
        compute=True,
        apiversion=get_active_settings().API_VERSION,
        fileversion=get_active_settings().FILE_VERSION,
    ):

        if apiversion == "0.1":
            if self.instance.unique is None:
                raise Exception("Please assign a Unique ID first")

            dataset = array.to_dataset(name="data")
            dataset.attrs["apiversion"] = apiversion
            dataset.attrs["fileversion"] = fileversion
            if fileversion == "0.1":
                dataset.attrs["model"] = str(self.instance.__class__.__name__)
                dataset.attrs["unique"] = str(self.instance.unique)
            else:
                raise NotImplementedError(
                    "This FileVersion has not been Implemented yet"
                )

        else:
            raise NotImplementedError("This API Version has not been Implemented Yet")

        try:
            logger.info(
                f"Saving File with API v.{apiversion}  and File v.{fileversion} "
            )
            return dataset.to_zarr(
                store=self.connected, mode="w", compute=compute, consolidated=True
            )
        except Exception as e:
            raise e

    def loadDataArray(self, apiversion=get_active_settings().API_VERSION):
        print(self.connected)
        dataset = xr.open_zarr(store=self.connected, consolidated=True)
        fileversion = dataset.attrs["fileversion"]
        logger.info(dataset)
        return dataset["data"]

    def loadDataset(self):
        return xr.open_zarr(store=self.connected, consolidated=False)
