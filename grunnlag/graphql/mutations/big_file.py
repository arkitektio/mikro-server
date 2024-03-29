from balder.types.scalars import ImageFile
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types, scalars
from grunnlag.enums import OmeroFileType
from pathlib import Path
from grunnlag.graphql.queries.request import s3
import ntpath
from grunnlag.scalars import AssignationID
import logging


from grunnlag.scalars import AssignationID
logger = logging.getLogger(__name__)


class UploadBigFile(BalderMutation):
    """Upload a file to Mikro

    This mutation uploads a file to Omero and returns the created OmeroFile.
    """
    class Arguments:
        file = scalars.BigFile(required=True)
        datasets = graphene.List(graphene.ID, required=False)
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(root, info, *args, file=None, name=None, experiments=None, datasets=None,  created_while=None, **kwargs):
        # do something with yosur file
   
        key = file
        ending = key.split(".")[-1]


        if ending in ["tiff", "tif", "TIF"]:
            filetype = OmeroFileType.TIFF
        elif ending in ["msr"]:
            filetype = OmeroFileType.MSR
        elif ending in ["jpeg", "jpg", "JPG"]:
            filetype = OmeroFileType.JPEG
        else:
            filetype = OmeroFileType.UNKNWON

        t = models.OmeroFile.objects.create(
            file=key, name=key, created_while=created_while, creator=info.context.user, type=filetype
        )

        if datasets:
            t.datasets.set(datasets)

        return t

    class Meta:
        type = types.OmeroFile