import graphene



class BalderRegistry:

    def __init__(self) -> None:
        
        self.queries = {}
        self.mutations = {}
        self.subscriptions = {}
        self.types = {}


    def registerQuery(self, query):
        self.queries[query._get_operation()] = query._to_field()

    def registerMutation(self, mutation):
        self.mutations[mutation._get_operation()] = mutation._to_field()

    def registerSubscription(self, subscription):
        self.subscriptions[subscription._get_operation()] = subscription._to_field()
    

    def buildSchema(self, query=None, mutation=None, subscription=None):
        assert issubclass(query, graphene.ObjectType) or query is None, "If you provide an additional root Query please make sure its of type Query"
        QueryBase = query if query is not None else graphene.ObjectType
        MutationBase = mutation if mutation is not None else graphene.ObjectType
        SubscriptionBase = subscription if subscription is not None else graphene.ObjectType

        query = type("Query", (QueryBase, ), {**self.queries, "__doc__": "The root Query"}) if self.queries != {} else None
        mutation = type("Mutation", (MutationBase, ), {**self.mutations, "__doc__": "The root Mutation"}) if self.mutations != {} else None
        subscription = type("Subscription", (SubscriptionBase, ), {**self.subscriptions, "__doc__": "The root Subscriptions"}) if self.subscriptions != {} else None


        return graphene.Schema(
            query = query,
            mutation = mutation,
            subscription = subscription,
        )




BALDER_REGISTRY = None

def get_balder_registry():
    global BALDER_REGISTRY
    if BALDER_REGISTRY is None:
        BALDER_REGISTRY = BalderRegistry()
    return BALDER_REGISTRY