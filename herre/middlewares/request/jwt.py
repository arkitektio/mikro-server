import asyncio
from herre.middlewares.utils import set_request_async, set_request_sync
from herre.utils import check_token_from_request
from django.utils.decorators import sync_and_async_middleware
import logging
from django.core.exceptions import  PermissionDenied
logger = logging.getLogger(__name__)



@sync_and_async_middleware
def JWTTokenMiddleWare(get_response):
    # One-time configuration and initialization goes here.
    if asyncio.iscoroutinefunction(get_response):
        async def middleware(request):
            # Do something here!
            try:
                decoded, token = check_token_from_request(request)
                if decoded:
                    request = await set_request_async(request, decoded, token)
            except Exception as e:
                logger.error(e)
                raise PermissionDenied(str(e))
            response = await get_response(request)
            return response

    else:
        def middleware(request):
            # Do something here!
            try:
                decoded, token = check_token_from_request(request)
                if decoded:
                    request = set_request_sync(request, decoded, token)
            except Exception as e:
                logger.error(e)
                raise PermissionDenied(str(e))
            response = get_response(request)
            return response

    return middleware