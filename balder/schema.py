from balder.types.query import BalderQuery
from herre.bouncer.utils import bounced
from balder.registry import get_balder_registry
import graphene

class Query(graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")

    @bounced()
    def resolve_hello(*args, **kwargs):
        return "Hallo"



graphql_schema = get_balder_registry().buildSchema(query = Query)