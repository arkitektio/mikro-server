from graphene_django.views import GraphQLView
from django.views.decorators.csrf import csrf_exempt

from django.views.decorators.clickjacking import xframe_options_exempt

try:
    import channels_graphql_ws
    GraphQLView.graphiql_template = "graphene/graphiql-ws.html"
except:
    pass


BalderView = xframe_options_exempt(csrf_exempt(GraphQLView.as_view(graphiql=True)))