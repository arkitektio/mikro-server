import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    ModelFilter,
)


class Models(BalderQuery):
    """All Labels
    
    This query returns all Labels that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Labels that the user has access to. If the user is an amdin
    or superuser, all Labels will be returned.
    """

    class Meta:
        list = True
        type = types.Model
        filter = ModelFilter
        paginate = True
        operation = "models"

class MyModels(BalderQuery):
    """My Experiments runs a fast query on the database to return all
    Experiments that the user has created. This query is faster than
    the `experiments` query, but it does not return all Experiments that
    the user has access to."""

    class Meta:
        list = True
        personal = "created_by"
        type = types.Model
        filter = ModelFilter
        paginate = True
        operation = "mymodels"


class Model(BalderQuery):
    """Get a single label by ID
    
    Returns a single label by ID. If the user does not have access
    to the label, an error will be raised."""

    class Arguments:
        id = graphene.ID(description="The ID to search by")

    def resolve(root, info, id):

        return models.Model.objects.get(id=id)

    class Meta:
        type = types.Model
        operation = "model"

