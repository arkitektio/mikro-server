from herre.middlewares.utils import set_scope_async
from herre.utils import decode_token
from herre.token import JwtToken
from django.utils.decorators import sync_and_async_middleware
import logging
from django.conf import settings
from django.core.exceptions import  PermissionDenied
from django.contrib.auth import get_user_model
import urllib
from asgiref.sync import async_to_sync, sync_to_async
logger = logging.getLogger(__name__)

UserModel = get_user_model()


class JWTChannelMiddleware:
    """
    Custom middleware (insecure) that takes user IDs from the query string.
    """

    def __init__(self, app):
        # Store the ASGI application we were passed
        self.app = app

    async def __call__(self, scope, receive, send):

        # Close old database connections to prevent usage of timed out connections
        # Look up user from query string (you should also do things like
        # check it's a valid user ID, or if scope["user"] is already populated)
        
        qs = urllib.parse.parse_qs(scope["query_string"].decode())
        if "token" in qs:
            try:
                token = qs["token"][0]
                decoded = decode_token(token)
                await set_scope_async(scope, decoded, token)
            except Exception as e:
                logger.error(e)  

        return await self.app(scope, receive, send)