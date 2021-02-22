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


@sync_to_async
def set_scope_async(scope, decoded):
    if "email" in decoded:
        try:
            user = UserModel.objects.get(email=decoded["email"])
        except UserModel.DoesNotExist:
            raise PermissionDenied("This user does not exist. Please create the User in the Database!!")
    else:
        user = None

    scope["auth"] = JwtToken(decoded, user)
    scope["user"] = user
    return scope


    

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
                decoded = decode_token(qs["token"][0])
                await set_scope_async(scope, decoded)
            except Exception as e:
                logger.error(e)  

        return await self.app(scope, receive, send)