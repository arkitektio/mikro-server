from django.conf import settings
import hashlib
from django.core.cache import cache
from storages.backends.s3boto3 import S3Boto3Storage


class PrivateRenderStorage(S3Boto3Storage):
    default_acl = "private"
    file_overwrite = False

    def url(self, name):
        # Add a prefix to avoid conflicts with any other apps
        key = f"PrivateMediaStorage_{name}"
        result = cache.get(key)
        if result:
            return result

        # No cached value exists, follow the usual logic
        result = super(PrivateRenderStorage, self).url(name)
        result = result.replace(settings.AWS_S3_ENDPOINT_URL, "")

        # Cache the result for 3/4 of the temp_url's lifetime.
        try:
            timeout = settings.AWS_QUERYSTRING_EXPIRE
        except:
            timeout = 3600
        timeout = int(timeout * 0.75)
        cache.set(key, result, timeout)

        return result
    
    def move_inside_bucket(self, bucket_key, new_key):
        # Move the file from the bucket to the private storage
        # This is used to move files from the public bucket to the private bucket

        # Get the file from the bucket
        copy_result = self.connection.meta.client.copy_object(
            Bucket=self.bucket_name,
            CopySource=bucket_key,
            Key=new_key)

        if copy_result['ResponseMetadata']['HTTPStatusCode'] == 200:
            True
        else:
            False