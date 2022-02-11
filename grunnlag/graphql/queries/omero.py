from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ExperimentFilter, OmeroFileFilter
from grunnlag import types, models


class OmeroFile(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.OmeroFile.objects.get(id=id)

    class Meta:
        type = types.OmeroFile
        operation = "omerofile"


class MyOmeroFiles(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        personal = "creator"
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "myomerofiles"


class OmeroFiles(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.OmeroFile
        filter = OmeroFileFilter
        paginate = True
        operation = "omerofiles"
