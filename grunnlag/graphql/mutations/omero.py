from balder.types.scalars import ImageFile
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types
from grunnlag.enums import OmeroFileType
from pathlib import Path
import ntpath


class UploadOmeroFile(BalderMutation):
    """Upload a file to Mikro

    This mutation uploads a file to Omero and returns the created OmeroFile.
    """
    class Arguments:
        file = ImageFile(required=True)
        name = graphene.String(required=False)
        experiments = graphene.List(graphene.ID, required=False)
        datasets = graphene.List(graphene.ID, required=False)

    @bounced()
    def mutate(root, info, *args, file=None, name=None, experiments=None, datasets=None,  **kwargs):
        # do something with your file

        filename: str = ntpath.basename(file.name)
        ending = filename.split(".")[-1]
        name = name or filename.split("/")[-1]

        if ending in ["tiff", "tif", "TIF"]:
            filetype = OmeroFileType.TIFF
        elif ending in ["msr"]:
            filetype = OmeroFileType.MSR
        elif ending in ["jpeg", "jpg", "JPG"]:
            filetype = OmeroFileType.JPEG
        else:
            filetype = OmeroFileType.UNKNWON

        t = models.OmeroFile.objects.create(
            file=file, name=name, creator=info.context.user, type=filetype
        )

        if experiments:
            t.experiments.set(experiments)

        if datasets:
            t.datasets.set(datasets)

        return t

    class Meta:
        type = types.OmeroFile


class DeleteOmeroFileResult(graphene.ObjectType):
    id = graphene.String()


class DeleteOmeroFile(BalderMutation):
    """Delete OmeroFile"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deleted File", required=True
        )

    @bounced()
    def mutate(root, info, id, **kwargs):
        om = models.OmeroFile.objects.get(id=id)
        om.delete()
        return {"id": id}

    class Meta:
        type = DeleteOmeroFileResult


class UpdateOmeroFile(BalderMutation):
    """Update an omero file"""

    class Arguments:
        id = graphene.ID(
            required=True, description="The omero file you want to update"
        )
        tags = graphene.List(graphene.String, required=False, description="The updated tags ( old tags will be deleted)")

    @bounced()
    def mutate(root, info, id, tags=[]):
        rep = models.OmeroFile.objects.get(id=id)
        if tags:
            rep.tags.set(tags)
        rep.save()
        return rep

    class Meta:
        type = types.OmeroFile
