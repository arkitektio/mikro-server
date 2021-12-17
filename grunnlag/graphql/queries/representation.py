from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    RepresentationFilter,
)


class MyRepresentations(BalderQuery):
    """My Representations returns all of the Representations, attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "myrepresentations"


class Representation(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Representation.objects.get(id=id)

    class Meta:
        type = types.Representation
        operation = "representation"


class Representations(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "representations"
