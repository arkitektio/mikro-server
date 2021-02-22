import graphene

class Query(graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")

graphql_schema = graphene.Schema(
    query=Query,
)