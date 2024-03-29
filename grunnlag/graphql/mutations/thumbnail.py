from balder.types.scalars import ImageFile
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types

from grunnlag.scalars import AssignationID

class UploadThumbnail(BalderMutation):
    class Arguments:
        file = ImageFile(required=True)
        major_color = graphene.String(required=False)
        blurhash = graphene.String(required=False)
        rep = graphene.ID(description="The repr", required=True)
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(root, info, *args, rep=None, file=None, major_color=None,  created_while=None, blurhash=None):
        # do something with your file
        print(file)

        t = models.Thumbnail.objects.create(representation_id=rep, created_while=created_while, blurhash=blurhash)
        t.image = file
        t.major_color = major_color
        t.save()
        print(file)
        return t

    class Meta:
        type = types.Thumbnail
