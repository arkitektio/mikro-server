from balder.types.scalars import ImageFile
from balder.types.mutation import BalderMutation
import graphene
from lok import bounced
from grunnlag import models, types

from grunnlag.scalars import AssignationID, BigFile

class UploadVideo(BalderMutation):
    class Arguments:
        file = BigFile(required=True)
        front_image = BigFile(required=False)
        representations = graphene.List(graphene.ID, description="The renderer representations", required=True)
        created_while = AssignationID(required=False, description="The assignation id")

    @bounced()
    def mutate(root, info, *args, representations=None, front_image=None, file=None, created_while=None):
        # do something with your file
        print(file)

        t = models.Video.objects.create(data=file, created_while=created_while)
        t.representations.set(representations)
        if front_image:
            t.front_image = front_image
        t.save()
        return t

    class Meta:
        type = types.Video
