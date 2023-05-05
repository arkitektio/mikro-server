from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import VideoFilter
from grunnlag import types, models


class VideoDetail(BalderQuery):
    """Get a single Thumbnail by ID
    
    Get a single Thumbnail by ID. If the user does not have access
    to the Thumbnail, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Video.objects.get(id=id)

    class Meta:
        type = types.Video
        operation = "video"


class Thumbnails(BalderQuery):
    """All Thumbnails
    
    This query returns all Thumbnails that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Thumbnails that the user has access to. If the user is an amdin
    or superuser, all Thumbnails will be returned.
    
    """

    class Meta:
        list = True
        type = types.Video
        filter = VideoFilter
        paginate = True
        operation = "videos"
