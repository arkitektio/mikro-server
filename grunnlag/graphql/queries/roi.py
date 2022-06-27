import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    ROIFilter,
    RepresentationFilter,
)


class Rois(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.ROI
        filter = ROIFilter
        paginate = True
        operation = "rois"


class Roi(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.ROI.objects.get(id=id)

    class Meta:
        type = types.ROI
        operation = "roi"
