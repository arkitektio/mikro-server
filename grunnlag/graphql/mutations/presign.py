from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types
from grunnlag.enums import ModelKind
from pathlib import Path
import ntpath
from grunnlag import models, types
from grunnlag.scalars import ModelFile
import logging
from grunnlag.utils import fill_created
from grunnlag.graphql.queries.request import s3
import os
logger = logging.getLogger(__name__)


class PresignedFields(graphene.ObjectType):
    key = graphene.String(required=True)
    x_amz_algorithm = graphene.String(required=True)
    x_amz_credential = graphene.String(required=True)
    x_amz_date = graphene.String(required=True)
    x_amz_signature = graphene.String(required=True)
    policy = graphene.String(required=True)

class Presigned(graphene.ObjectType):
    bucket = graphene.String(required=True)
    fields = graphene.Field(PresignedFields, required=True)


class Presign(BalderMutation):
    """Presign a file for upload"""

    class Arguments:
        file= graphene.String(required=True)

    @classmethod
    def mutate(cls, root, info, file=None):

        file = ntpath.normpath(file)
        file = os.path.basename(file)
        print(file)

        response = s3.generate_presigned_post("mikromedia",
                                                     file,
                                                     Fields=None,
                                                     Conditions=None,
                                                     ExpiresIn=3600)
        
        print(response)


        return  {"bucket": "mikromedia", "fields": {key.replace("-","_"): value for key, value in response["fields"].items()}}

    class Meta:
        type = Presigned
        operation = "presign"