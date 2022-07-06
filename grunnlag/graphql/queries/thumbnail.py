from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ThumbnailFilter
from grunnlag import types, models


class ThumbnailDetail(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Thumbnail.objects.get(id=id)

    class Meta:
        type = types.Thumbnail
        operation = "thumbnail"


class Thumbnails(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Thumbnail
        filter = ThumbnailFilter
        paginate = True
        operation = "thumbnails"
