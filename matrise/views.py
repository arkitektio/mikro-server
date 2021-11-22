# Create your views here.
import json

import xarray as xr
from django.http import FileResponse, HttpResponse
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from zarr.storage import (array_meta_key, attrs_key, default_compressor,
                          group_meta_key)

import logging

from .models import Matrise

logger = logging.getLogger(__name__)

# Zarr Specific Settings
zarr_metadata_key = '.zmetadata'
api_array = "array"


class MatriseViewsetMixIn():
    lookup_value_regex = '[^/]+'
    download_permission = '[^/]+'

    def arraySelect(self,request):
        matrise: Matrise = self.get_object()
        query_params = request.query_params
        array = matrise.array
        # We are trying to pass on selection params
        array = self.queryselect(array, query_params)
        return array

    def datasetSelect(self,request):
        matrise: Matrise = self.get_object()
        query_params = request.query_params
        dataset = matrise.dataset
        return dataset

    def queryselect(self, array: xr.DataArray, query_params: dict) -> xr.DataArray:
        """Selects the Array Acording to some query parameters
        
        Arguments:
            array {xr.DataArray} -- "The xr.DataArray to select from"
            query_params {dict} -- "The params according to Django QueryDicts"
        
        Raises:
            APIException: An APIExpection
        
        Returns:
            xr.DataArray -- The selected xr.DataArray 
        """
        try:
            array = array.sel(c=query_params["c"]) if "c" in query_params else array
            array = array.sel(t=query_params["t"]) if "t" in query_params else array
            if "channel_name" in query_params:
                s = f'Name == "{query_params["channel_name"]}"'
                c = array.biometa.channels.compute().query(s).index
                array = array.sel(c= c)
        except Exception as e:
            raise APIException(e)
        return array

    @action(methods=['get'], detail=True,
            url_path='shape', url_name='shape')
    def shape(self, request, pk):
        # We are trying to pass on selection params
        array = self.arraySelect(request)
        answer = json.dumps(array.shape)
        response = HttpResponse(answer, content_type="application/json")
        return response

    @action(methods=['get'], detail=True,
            url_path='dims', url_name='dims')
    def dims(self, request, pk):
        # We are trying to pass on selection params
        array = self.arraySelect(request)
        answer = json.dumps(array.dims)
        response = HttpResponse(answer, content_type="application/json")
        return response



    @action(methods=['get'], detail=True,
            url_path='channels', url_name='channels')
    def channels(self, request, pk):
        # We are trying to pass on selection params
        array = self.arraySelect(request)
        answer = array.biometa.channels.compute().to_json(orient="records")
        response = HttpResponse(answer, content_type="application/json")
        return response

    @action(methods=['get'], detail=True,
            url_path='info', url_name='info')
    def info(self, request, pk):
        # We are trying to pass on selection params
        array: xr.DataArray = self.arraySelect(request)
        with xr.set_options(display_style='html'):
            answer = array._repr_html_()
        response = HttpResponse(answer,  content_type="text/html")
        return response

    def returnFile(self, key: str, subkey: str) -> FileResponse:
        """Returns the FIle in the Store as a File Response 
        
        Arguments:
            key {string} -- key of the xr.Array Variable
            subkey {string} -- subkey of the chunk
        
        Returns:
            [FileResponse] -- The streaming HTTP FileReponse
        """
        matrise: Matrise = self.get_object()
        test = matrise.store.storage.open(f"{matrise.store.name}/{key}/{subkey}","rb")
        return FileResponse(test)

    @action(methods=['get'], detail=True,
            url_path=f'{api_array}/{zarr_metadata_key}', url_name=f'{api_array}/{zarr_metadata_key}')
    def get_zmetadata(self, request, pk):
        matrise: Matrise = self.get_object()
        test = matrise.store.storage.open(f"{matrise.store.name}/{zarr_metadata_key}","r")
        file_content = test.read()
        test.close()

        return HttpResponse(content=file_content, content_type="application/json")


    @action(methods=['get'], detail=True,
            url_path=f'{api_array}/{group_meta_key}', url_name=f'{api_array}/{group_meta_key}')
    def get_zgroupdata(self, request, pk):
        matrise: Matrise = self.get_object()
        test = matrise.store.storage.open(f"{matrise.store.name}/{group_meta_key}","r")
        file_content = test.read()
        test.close()

        return HttpResponse(content=file_content, content_type="application/json")

    @action(methods=['get'], detail=True,
            url_path=f'{api_array}/{attrs_key}', url_name=f'{api_array}/{attrs_key}')
    def get_zattrs(self, request, pk):
        matrise: Matrise = self.get_object()
        test = matrise.store.storage.open(f"{matrise.store.name}/{attrs_key}","r")
        file_content = test.read()
        test.close()

        return HttpResponse(content=file_content, content_type="application/json")

    @action(methods=['get'], detail=True,
            url_path=f'{api_array}/(?P<c_key>[^/.]+)/(?P<c_value>[^/]+)', url_name=f'{api_array}/arrayaccessor')
    def get_data_key(self, request, c_key, c_value,  pk):
        return  self.returnFile(c_key,c_value)
