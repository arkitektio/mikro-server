from django.contrib.auth import get_user_model
from balder.types.object import BalderObject
from plotql import models
from django.contrib.auth.models import Group as GroupModel
from balder.registry import register_type
from graphene.types.generic import GenericScalar


class Plot(BalderObject):
    """ A plot is a template to generate a graph
    
    Its store a PlotQL query and a list of variables that can be used in the
    query. The variables are stored as a JSON object. The variables are
    validated against the query before the query is executed.

    This query then returns a graph that can be rendered in the frontend.
    
    """
    class Meta:
        model = models.Plot
