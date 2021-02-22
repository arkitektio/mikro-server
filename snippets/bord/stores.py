
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db.models.fields.files import FieldFile
from storages.backends.s3boto3 import S3Boto3Storage
import logging
import pandas as pd
logger = logging.getLogger(__name__)


class ParquetStore(FieldFile):

    def _openDataframe(self):
        if isinstance(self.storage, S3Boto3Storage):
            bucket = self.storage.bucket_name
            location = self.storage.location
            parquet = f"{bucket}/{self.name}"
            # Initilize the S3 file system
            path = self.url.split("http://")
            logger.info(f"Bucket [{bucket}]: Connecting to {self.name}")
            df = pd.read_parquet(path) # needs to be prepended with s3, check?
            return df
        if isinstance(self.storage, FileSystemStorage):
            location = self.storage.location
            path = f"{location}/{self.name}"
            # Initilize the S3 file system
            logger.info(f"Folder [{location}]: Connecting to {self.name}")
            df = pd.read_parquet(path)
            return df
        else:
            raise NotImplementedError("Other Storage Formats have not been established yet. Please use S3 like Storage for time being")

    def _writeToDataframe(self, df, compute= True):
        if isinstance(self.storage, S3Boto3Storage):
            bucket = self.storage.bucket_name
            location = self.storage.location
            parquet = f"{bucket}/{self.name}"
            # Initilize the S3 file system
            path = self.url.split("http://")
            logger.info(f"Bucket [{bucket}]: Connecting to {self.name}")
            df = df.to_parquet(df, path, compute=compute) # needs to be prepended with s3, check?
            return df
        if isinstance(self.storage, FileSystemStorage):
            location = self.storage.location
            path = f"{location}/{self.name}"
            # Initilize the S3 file system
            logger.info(f"Folder [{location}]: Connecting to {self.name}")
            df = df.to_parquet(df, path, compute=compute)
            return df
        else:
            raise NotImplementedError("Other Storage Formats have not been established yet. Please use S3 like Storage for time being")

    def save(self, df, compute=True):
        return self._writeToDataframe(df, compute=compute)

    def load(self):
        return self._openDataframe()


