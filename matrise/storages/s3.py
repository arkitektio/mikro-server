from storages.backends.s3boto3 import S3Boto3Storage
from storages.base import BaseStorage
from ..settings import get_active_settings

active_settings = get_active_settings()



class S3Storage(S3Boto3Storage, BaseStorage):
    bucket_name = active_settings.STORAGE_BUCKET
    custom_domain = f"{active_settings.S3_PUBLIC_URL}/{bucket_name}"


