from bord.enums import PandasDType
from bord import models
from grunnlag import types
from balder.types.query.base import BalderQuery
import graphene
from bord.filters import GraphFilter


class Graphs(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.Graph
        filter = GraphFilter
        paginate = True
        operation = "graphs"


class MyGraphs(BalderQuery):
    """My samples return all of the users samples attached to the current user"""

    class Meta:
        list = True
        type = types.Graph
        filter = GraphFilter
        personal = "creator"
        paginate = True
        operation = "mygraphs"


class Graph(BalderQuery):
    """Get a single representation by ID"""

    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: models.Graph.objects.get(id=id)

    class Meta:
        type = types.Graph
        operation = "graph"


