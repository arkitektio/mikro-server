# Create your views here.
import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from dask.dataframe.core import DataFrame
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.schemas.openapi import AutoSchema
from rest_framework.serializers import Serializer
import logging

logger = logging.getLogger(__name__)

# Zarr Specific Settings
head_rows = 5

class BordViewSetMixIn():

    def dataFrameSelect(self,request):
        larvik = self.get_object()
        query_params = request.query_params
        df = larvik.bord
        # We are trying to pass on selection params
        return df


    @action(methods=['get'], detail=True,
            url_path='head', url_name='head')
    def head(self, request, pk):
        # We are trying to pass on selection params
        df: DataFrame = self.dataFrameSelect(request)
        answer = df.head(n=head_rows).compute(scheduler="threads").to_json(orient='records')
        response = HttpResponse(answer, content_type="application/json")
        return responses

    
