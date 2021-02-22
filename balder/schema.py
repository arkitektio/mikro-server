from balder.types.query import BalderQuery
from herre.bouncer.utils import bounced
import graphene

class Query(BalderQuery):
    hello = graphene.String(default_value="Hi!")

    @bounced()
    def resolve_hello(*args, **kwargs):
        return "Hallo"








graphql_schema = graphene.Schema(
    query=Query,
)