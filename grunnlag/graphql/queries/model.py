import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    ImageToImageModelFilter,
)


class ImageToImageModels(BalderQuery):
    """All Labels
    
    This query returns all Labels that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Labels that the user has access to. If the user is an amdin
    or superuser, all Labels will be returned.
    """

    class Meta:
        list = True
        type = types.ImageToImageModel
        filter = ImageToImageModelFilter
        paginate = True
        operation = "imageToImageModels"


class ImageToImageModel(BalderQuery):
    """Get a single label by ID
    
    Returns a single label by ID. If the user does not have access
    to the label, an error will be raised."""

    class Arguments:
        id = graphene.ID(description="The ID to search by")

    def resolve(root, info, id):

        return models.ImageToImageModel.objects.get(id=id)

    class Meta:
        type = types.ImageToImageModel
        operation = "imageToImageModel"

