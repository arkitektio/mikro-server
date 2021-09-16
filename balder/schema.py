from balder.types.query import BalderQuery
from lok import bounced
from balder.registry import get_balder_registry
from balder.autodiscover import autodiscover
import graphene

class Query(graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")

    @bounced()
    def resolve_hello(*args, **kwargs):
        return "Hallo"



autodiscover()
graphql_schema = get_balder_registry().buildSchema(query = Query)