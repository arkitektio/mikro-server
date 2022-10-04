from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ExperimentFilter, OmeroFileFilter
from grunnlag import types, models


class OmeroFile(BalderQuery):
    """Get a single Omero File by ID
    
    Returns a single Omero File by ID. If the user does not have access
    to the Omero File, an error will be raised."""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.OmeroFile.objects.get(id=id)

    class Meta:
        type = types.OmeroFile
        operation = "omerofile"


class MyOmeroFiles(BalderQuery):
    """My Omerofiles runs a fast query on the database to return all
    Omerofile that the user has created. This query is faster than
    the `omerofiles` query, but it does not return all OmeroFile that
    the user has access to."""

    class Meta:
        list = True
        personal = "creator"
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "myomerofiles"


class OmeroFiles(BalderQuery):
    """All OmeroFiles

    This query returns all OmeroFiles that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all OmeroFiles that the user has access to. If the user is an amdin
    or superuser, all OmeroFiles will be returned.
    
    """

    class Meta:
        list = True
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "omerofiles"
