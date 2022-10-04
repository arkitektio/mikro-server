import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    FeatureFilter,
    LabelFilter,
)


class Features(BalderQuery):
    """All features
    
    This query returns all features that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all features that the user has access to. If the user is an amdin
    or superuser, all features will be returned.
    """

    class Meta:
        list = True
        type = types.Feature
        filter = FeatureFilter
        paginate = True
        operation = "features"


class Feature(BalderQuery):
    """Get a single feature by ID
    
    Returns a single feature by ID. If the user does not have access
    to the feature, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by")

    def resolve(root, info, id):

        return models.Feature.objects.get(id=id)

    class Meta:
        type = types.Feature
        operation = "feature"
