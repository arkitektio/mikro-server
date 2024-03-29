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
import logging
from guardian.shortcuts import get_objects_for_user, get_objects_for_group
from django.contrib.auth.models import User, Group
from django.contrib.auth import get_user_model


class MyRepresentations(BalderQuery):
    """My Representatoin runs a fast query on the database to return all
    Representation that the user has created. This query is faster than
    the `representations` query, but it does not return all Representation that
    the user has access to."""

    class Meta:
        list = True
        personal = "creator"
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "myrepresentations"


class AccessibleRepresentations(BalderQuery):
    def resolve(self, info):
        reps = get_objects_for_user(
            info.context.user,
            "grunnlag.download_representation",
        )
        return reps.all()

    class Meta:
        type = types.Representation
        list = True
        operation = "accessiblerepresentations"


class SharedRepresentations(BalderQuery):
    def resolve(self, info):
        reps = get_objects_for_user(
            info.context.user,
            "grunnlag.download_representation",
        ).exclude(creator=info.context.user)
        return reps.all()

    class Meta:
        type = types.Representation
        list = True
        operation = "sharedrepresentations"


class RepresentationsForGroup(BalderQuery):
    class Arguments:
        name = graphene.String(description="The Group to search by", required=True)

    def resolve(self, info, name):
        group = info.context.user.groups.get(name=name)

        reps = get_objects_for_group(
            group,
            "grunnlag.download_representation",
        )
        return reps.all()

    class Meta:
        type = types.Representation
        list = True
        operation = "representationsForGroup"


class RepresentationsForUser(BalderQuery):
    class Arguments:
        email = graphene.String(description="The Person you shared for", required=True)

    def resolve(self, info, email):
        user = get_user_model().objects.get(email=email)

        reps = get_objects_for_user(
            user,
            "grunnlag.download_representation",
        ).filter(creator=info.context.user)
        return reps.all()

    class Meta:
        type = types.Representation
        list = True
        operation = "representationsForUser"


class Representation(BalderQuery):
    """Get a single Representation by ID

    Returns a single Representation by ID. If the user does not have access
    to the Representation, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        rep = models.Representation.objects.get(id=id)
        # assert rep.creator == info.context.user or info.context.user.has_perm(
        #     "grunnlag.view_representation", rep
        # ), "You do not have permission to view this representation"

        return models.Representation.objects.get(id=id)

    class Meta:
        type = types.Representation
        operation = "representation"


class Image(BalderQuery):
    """Get a single Image by ID

    Returns a single Representation by ID. If the user does not have access
    to the Representation, an error will be raised.
    """

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    def resolve(self, info, id):
        rep = models.Representation.objects.get(id=id)
        # assert rep.creator == info.context.user or info.context.user.has_perm(
        #     "grunnlag.view_representation", rep
        # ), "You do not have permission to view this representation"

        return models.Representation.objects.get(id=id)

    class Meta:
        type = types.Representation
        operation = "image"


def get_random_obj_from_queryset(queryset):
    max_pk = queryset.aggregate(max_pk=Max("pk"))["max_pk"]
    if max_pk is None:
        return None
    while True:
        obj = queryset.filter(pk=random.randint(1, max_pk)).first()
        if obj:
            return obj


class RandomRep(BalderQuery):
    """Get a random Representation

    Gets a random Representation from the database. This is used for
    testing purposes

    """

    resolve = lambda root, info: get_random_obj_from_queryset(
        models.Representation.objects.all()
    )

    class Meta:
        type = types.Representation
        operation = "random_representation"


class Representations(BalderQuery):
    """All Representations

    This query returns all Representations that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Representations that the user has access to. If the user is an amdin
    or superuser, all Representations will be returned."""

    class Meta:
        list = True
        type = types.Representation
        filter = RepresentationFilter
        paginate = True
        operation = "representations"
