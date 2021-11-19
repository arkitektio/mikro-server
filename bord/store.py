from django.core.files.storage import FileSystemStorage
from django.db.models.fields.files import FieldFile
from storages.backends.s3boto3 import S3Boto3Storage
import logging
from django.conf import settings


logger = logging.getLogger(__name__)


class FieldParquet(FieldFile):
    def _getFileSystem(self):

        if isinstance(self.storage, S3Boto3Storage):
            import s3fs

            bucket = self.storage.bucket_name
            location = self.storage.location
            s3_path = f"{bucket}/{self.name}"
            # Initilize the S3 file system
            logger.info(f"Bucket [{bucket}]: Connecting to {s3_path}")
            return s3fs.S3FileSystem(
                client_kwargs={"endpoint_url": settings["BORD"].S3_ENDPOINT_URL},
                key=settings["BORD"].ACCESS_KEY,
                secret=settings["BORD"].SECRET_KEY,
            )

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
            fs = self._getFileSystem()
            fs.rm(f"{bucket}/{self.name}/", recursive=True)
            return
        else:
            raise NotImplementedError(
                "Other Storage Formats have not been established yet. Please use S3 like Storage for time being"
            )