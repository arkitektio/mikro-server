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
