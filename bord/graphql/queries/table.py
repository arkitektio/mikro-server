from bord.enums import PandasDType
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


class ColumnsOf(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)
        dtype = graphene.List(
            PandasDType, description="Filter by dtype", required=False
        )
        name = graphene.String(description="Filter by name", required=False)

    def resolve(root, info, id, name=None, dtype=None):
        table = models.Table.objects.get(id=id)

        columns_data = table.store.data.read().schema.pandas_metadata["columns"]
        if name:
            columns_data = [c for c in columns_data if c["name"].startswith(name)]
        if dtype:
            columns_data = [c for c in columns_data if c["pandas_type"] in dtype]

        return columns_data

    class Meta:
        type = types.Column
        list = True
        operation = "columnsof"
