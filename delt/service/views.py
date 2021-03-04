from typing import List
from delt.settings import get_active_settings
from .types import DataPoint, DataQuery, Extension
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.exceptions import SuspiciousOperation
import re
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

tokenstring = re.compile(r"Bearer: (?P<token>.*)")


def DataModelViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class DataModelView(APIView):
        """Data Model View is returning all of the acessible models for the Arnheim platform
        """

        def get(self, request):
            models = [ model.dict() for model in registry.models ]
            return Response(models)
                    

    return DataModelView


def DataPointViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class DataPointView(APIView):
        """Data Model View is returning all of the acessible models for the Arnheim platform
        """

        def get(self, request):
            settings = get_active_settings()

            datapoint = DataPoint(
                inward = settings.inward,
                outward = settings.outward,
                port = settings.port,
                type = settings.type
            )


            return Response(datapoint.dict())
                    
    return DataPointView


def DataQueryViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class DataQueryView(APIView):
        """Data Model View is returning all of the acessible models for the Arnheim platform
        """

        def get(self, request):
            settings = get_active_settings()

            datapoint = DataPoint(
                inward = settings.inward,
                outward = settings.outward,
                port = settings.port,
                type = settings.type,
            )

            models = [ model.dict() for model in registry.models ]

            query = DataQuery(
                point = datapoint,
                models = models
            )


            return Response(query.dict())
                    
    return DataQueryView


def ExtensionsViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class ExtensionsView(APIView):
        """Data Model View is returning all of the acessible models for the Arnheim platform
        """

        def get(self, request):
            extensions: List[Extension] = registry.buildExtensionsForRequest(request)


            return Response([ex.dict() for ex in extensions])
                    
    return ExtensionsView


def ArkitektViewBuilder(registry):

    @method_decorator(csrf_exempt, name='dispatch')
    class ExtensionsView(APIView):
        """Data Model View is returning all of the acessible models for the Arnheim platform
        """

        def get(self, request):
            settings = get_active_settings()


            return Response(settings.service.dict())
                    
    return ExtensionsView





