
from graphene_django.views import GraphQLView
from django.views.decorators.csrf import csrf_exempt

try:
    import channels_graphql_ws
    GraphQLView.graphiql_template = "graphene/graphiql-ws.html"
except:
    pass



BalderView = csrf_exempt(GraphQLView.as_view(graphiql=True))