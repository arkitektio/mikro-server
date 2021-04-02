from typing import List
from delt.settings import get_active_settings
from .types import DataPoint, DataQuery
from rest_framework.response import Response
from rest_framework.views import APIView

from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import logging
from herre.bouncer.utils import bounced_request



logger = logging.getLogger(__name__)



def ArkitektViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class ArkitektView(APIView):
        """Arkitekt gives a short descriptor of the protocols that this service exhibits
        """

        def get(self, request):
            settings = get_active_settings()
            logger.info(f"Arkitekt Discovery: {settings.service}")
            return Response(settings.service.dict())
                    
    return ArkitektView




def PointViewBuilder(registry):
    models = [ model.dict() for model in registry.models ]

    settings = get_active_settings()

    datapoint = DataPoint(
        inward = settings.inward,
        outward = settings.outward,
        port = settings.port,
        type = settings.point_type,
    )

    models = [ model.dict() for model in registry.models ]

    query = DataQuery(
        version = settings.version,
        point = datapoint,
        models = models
    )

    @method_decorator(csrf_exempt, name='dispatch')
    class PointView(APIView):
        """Arkitekt gives a short descriptor of the protocols that this service exhibits
        """

        def post(self, request):
            logger.info(f"Arkitekt Point-Registration: {query}")
            return Response(query.dict())
                    
    return PointView


def ProviderViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class ProviderView(APIView):
        """Arkitekt gives a short descriptor of the protocols that this service exhibits
        """

        def post(self, request):
            logger.info(f"Arkitekt Provider-Registration: {request.data}")
            return Response({"ok": True})
    
                    
    return ProviderView


def NegotiateViewBuilder(registry):

    class NegotiateView(APIView):
        """Negotiate view will receive post requests on each negotiate (its like a hook)
        """
        permission_classes = ()
        authentication_classes = ()

        def post(self, request):
            logger.info(f"Arkitekt Provider-Registration: {request.data}")
            json_answer = registry.on_negotiate(request)
            return Response(json_answer)
                  
    return NegotiateView



