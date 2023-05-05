from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ExperimentFilter, OmeroFileFilter
from grunnlag import types, models
from django.conf import settings
import logging

logger = logging.getLogger(__name__)
import boto3
import json

sts = boto3.client('sts', 
    endpoint_url=settings.AWS_S3_ENDPOINT_URL,
    region_name='us-east-1',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    aws_session_token=None,
    config=boto3.session.Config(signature_version='s3v4'),
    verify=False
)

s3 = boto3.client('s3',
    endpoint_url=settings.AWS_S3_ENDPOINT_URL,
    region_name='us-east-1',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    aws_session_token=None,
    config=boto3.session.Config(signature_version='s3v4'),
    verify=False
)



class Credentials(graphene.ObjectType):
    status: str = graphene.String(required=True)
    access_key: str = graphene.String(required=True)
    secret_key: str = graphene.String(required=True)
    session_token: str = graphene.String(required=True)


class Request(BalderQuery):
    """Requets a new set of credentials from the S3 server
    encompassing the users credentials and the access key and secret key"""

    class Arguments:
        id = graphene.String(required=False)

    @classmethod
    def resolve(cls, root, info, id=None):

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowAllS3ActionsInUserFolder",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:*"],
                    "Resource": f"arn:aws:s3:::*"
                },
            ]
        }

        response = sts.assume_role(
                RoleArn='arn:xxx:xxx:xxx:xxxx',
                RoleSessionName='sdfsdfsdf',
                Policy=json.dumps(policy, separators=(',',':')),
                DurationSeconds=40000
            )

        print(response)

        aws =  {
            "access_key": response["Credentials"]["AccessKeyId"],
            "secret_key": response["Credentials"]["SecretAccessKey"],
            "session_token": response["Credentials"]["SessionToken"],
            "status": "success"
        }

        return  aws

    class Meta:
        type = Credentials
        operation = "request"


