from balder.types.query import BalderQuery
import graphene
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from plotql import types, models, filters
from balder.registry import register_type
import graphene


class Plot(BalderQuery):
    class Arguments:
        id = graphene.ID(required=False, description="The id of the plot")

    def resolve(self, info, id):
        return models.Plot.objects.get(id=id)

    class Meta:
        type = types.Plot
        operation = "plot"


class MyPlots(BalderQuery):
    class Meta:
        type = types.Plot
        filter= filters.PlotFilter
        paginate = True
        list = True
        operation = "myplots"
