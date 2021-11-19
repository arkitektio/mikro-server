from storages.backends.s3boto3 import S3Boto3Storage
from storages.base import BaseStorage
from django.conf import settings


class S3Storage(S3Boto3Storage, BaseStorage):
    bucket_name = settings.BORD["BUCKET"]
    custom_domain = f"{settings.BORD['PUBLIC_URL']}/{bucket_name}"
