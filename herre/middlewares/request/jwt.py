import asyncio
from herre.utils import check_token_from_request
from herre.token import JwtToken
from django.utils.decorators import sync_and_async_middleware
import logging
from django.conf import settings
from django.core.exceptions import  PermissionDenied
from django.contrib.auth import get_user_model

from asgiref.sync import async_to_sync, sync_to_async
logger = logging.getLogger(__name__)

UserModel = get_user_model()


@sync_to_async
def set_request_async(request, decoded):
    if "email" in decoded:
        try:
            user = UserModel.objects.get(email=decoded["email"])
        except UserModel.DoesNotExist:
            raise PermissionDenied("This user does not exist. Please create the User in the Database!!")
    else:
        user = None

    request.auth = JwtToken(decoded, user)
    request.user = user
    return request


def set_request_sync(request, decoded):
    if "email" in decoded:
        try:
            user = UserModel.objects.get(email=decoded["email"])
        except UserModel.DoesNotExist:
            raise PermissionDenied("This user does not exist. Please create the User in the Database!!")
    else:
        user = None

    request.auth = JwtToken(decoded, user)
    request.user = user
    return request
    



@sync_and_async_middleware
def JWTTokenMiddleWare(get_response):
    # One-time configuration and initialization goes here.
    if asyncio.iscoroutinefunction(get_response):
        async def middleware(request):
            # Do something here!
            decoded = check_token_from_request(request)
            if decoded:
                request = await set_request_async(request, decoded)
            response = await get_response(request)
            return response

    else:
        def middleware(request):
            # Do something here!
            decoded = check_token_from_request(request)
            if decoded:
                request = set_request_sync(request, decoded)
            response = get_response(request)
            return response

    return middleware