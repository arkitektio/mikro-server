from django.db import reset_queries
from balder.types.mutation.base import BalderMutation
import graphene
from grunnlag import types, models
from balder.types.query.base import BalderQuery
from grunnlag.filters import (
    RepresentationFilter,
)
from django.db.models import Max
import random


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


def get_random_obj_from_queryset(queryset):
    max_pk = queryset.aggregate(max_pk=Max("pk"))["max_pk"]
    if max_pk is None:
        return None
    while True:
        obj = queryset.filter(pk=random.randint(1, max_pk)).first()
        if obj:
            return obj


class RandomRep(BalderQuery):
    """Get a single representation by ID"""

    resolve = lambda root, info: get_random_obj_from_queryset(
        models.Representation.objects.all()
    )

    class Meta:
        type = types.Representation
        operation = "random_representation"


class Representations(BalderQuery):
    """All represetations"""

    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "representations"
