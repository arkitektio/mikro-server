import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    ROIFilter,
    RepresentationFilter,
)


class Rois(BalderQuery):
    """All Rois
    
    This query returns all Rois that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Rois that the user has access to. If the user is an amdin
    or superuser, all Rois will be returned."""

    class Meta:
        list = True
        type = types.ROI
        filter = ROIFilter
        paginate = True
        operation = "rois"


class Roi(BalderQuery):
    """Get a single Roi by ID"
    
    Returns a single Roi by ID. If the user does not have access
    to the Roi, an error will be raised."""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.ROI.objects.get(id=id)

    class Meta:
        type = types.ROI
        operation = "roi"
