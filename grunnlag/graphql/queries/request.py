from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ExperimentFilter, OmeroFileFilter
from grunnlag import types, models
from django.conf import settings
import logging

logger = logging.getLogger(__name__)
import requests


class Credentials(graphene.ObjectType):
    status: str = graphene.String()
    access_key: str = graphene.String()
    secret_key: str = graphene.String()


class Request(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.String(required=False)

    @classmethod
    def resolve(cls, root, info, id=None):
        import boto3

        client = boto3.client(
            "sts",
            region_name="us-west-2",
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        return {
            "access_key": settings.AWS_ACCESS_KEY_ID,
            "secret_key": settings.AWS_SECRET_ACCESS_KEY,
        }

    class Meta:
        type = Credentials
        operation = "request"
