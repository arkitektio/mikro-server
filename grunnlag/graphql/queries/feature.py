import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    FeatureFilter,
    LabelFilter,
)


class Features(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Feature
        filter = FeatureFilter
        paginate = True
        operation = "features"


class Feature(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by")

    def resolve(root, info, id):

        return models.Feature.objects.get(id=id)

    class Meta:
        type = types.Feature
        operation = "feature"
