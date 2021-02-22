from django.test.client import RequestFactory 
import re
from channels.db import database_sync_to_async
import urllib
from oauth2_provider.models import AccessToken
import logging

tokenreg = re.compile(r".*token=(?P<token>.*)\'")
logger = logging.getLogger()

@database_sync_to_async
def get_user(authenticator, request):
    return authenticator.authenticate(request)


@database_sync_to_async
def setScopeWithAuth(scope, token):
    try:
        auth = AccessToken.objects.select_related("user","application").get(token=token)
        scope["auth"] = auth
        scope["user"] = auth.user

        scope["bouncer"] = {}

    except AccessToken.DoesNotExist:
        logger.error("User tried signing in with a Accesstoken that does not exist")

    return scope


class ApolloAuthTokenMiddleware:
    """
    Custom middleware (insecure) that takes application token from the query string.
    """

    def __init__(self, app):
        # Store the ASGI application we were passed
        self.app = app

    async def __call__(self, scope, receive, send):

        # Close old database connections to prevent usage of timed out connections
        # Look up user from query string (you should also do things like
        # check it's a valid user ID, or if scope["user"] is already populated)
        user = scope["user"]
        
        try:
            qs = urllib.parse.parse_qs(scope["query_string"].decode())
            print(qs)
            if "token" in qs:
                # compatibility with rest framework
                auth_token = qs["token"][0] # We are dealing with a list get the first one

                scope = await setScopeWithAuth(scope, auth_token)
            
        except Exception as e:
            logger.error(e)  


        return await self.app(scope, receive, send)

