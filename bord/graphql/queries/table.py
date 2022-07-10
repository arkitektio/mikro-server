from bord import models
from grunnlag import types
from balder.types.query.base import BalderQuery
import graphene
from bord.filters import TableFilter


class Tables(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.Table
        filter = TableFilter
        paginate = True
        operation = "tables"


class MyTables(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.Table
        filter = TableFilter
        personal = "creator"
        paginate = True
        operation = "mytables"


class Table(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Table.objects.get(id=id)

    class Meta:
        type = types.Table
        operation = "table"
