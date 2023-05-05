from balder.types.query.base import BalderQuery
from graphene import types
import graphene
from grunnlag.filters import ContextFilter
from grunnlag import types, models
from itertools import chain
class ProvenanceResult(graphene.Union):

    class Meta:
        types = (types.Context, types.Experiment, types.Representation, types.ROI, types.Sample, types.Stage, types.Table)


class Provenance(BalderQuery):
    """All Experiments
    
    This query returns all Experiments that are stored on the platform
    depending on the user's permissions. Generally, this query will return
    all Experiments that the user has access to. If the user is an amdin
    or superuser, all Experiments will be returned.

    If you want to retrieve only the Experiments that you have created,
    use the `myExperiments` query.
    
    """

    class Arguments:
        created_whiles = graphene.List(graphene.ID, required=True)
    

    def resolve(self, info, created_whiles):
        e = models.Experiment.objects.filter(created_while__in=created_whiles).all()
        r = models.Representation.objects.filter(created_while__in=created_whiles).all()
        rois = models.ROI.objects.filter(created_while__in=created_whiles).all()
        s = models.Sample.objects.filter(created_while__in=created_whiles).all()
        st = models.Stage.objects.filter(created_while__in=created_whiles).all()

        return chain(e,r, rois, s, st)

    class Meta:
        list = True
        type = ProvenanceResult
        filter = ContextFilter
        operation = "provenance"