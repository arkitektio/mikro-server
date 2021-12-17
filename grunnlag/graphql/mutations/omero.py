from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types
from grunnlag.enums import OmeroFileType


class UploadOmeroFile(BalderMutation):
    class Arguments:
        file = Upload(required=True)

    @bounced()
    def mutate(
        root,
        info,
        *args,
        file=None,
    ):
        # do something with your file

        filename: str = file.name

        ending = filename.split(".")[-1]
        name = filename.split(".")[0]

        print(ending)

        if ending in ["tiff", "tif", "TIF"]:
            filetype = OmeroFileType.TIFF
        elif ending in ["msr"]:
            filetype = OmeroFileType.MSR
        elif ending in ["jpeg", "jpg", "JPG"]:
            filetype = OmeroFileType.JPEG
        else:
            filetype = OmeroFileType.UNKNWON

        print(file)
        t = models.OmeroFile.objects.create(
            file=file, name=name, creator=info.context.user, type=filetype
        )

        file.name

        print(file)
        return t

    class Meta:
        type = types.OmeroFile


class DeleteOmeroFileResult(graphene.ObjectType):
    id = graphene.String()


class DeleteOmeroFile(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deletet Representation", required=True
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        om = models.OmeroFile.objects.get(id=id)
        om.delete()
        return {"id": id}

    class Meta:
        type = DeleteOmeroFileResult
