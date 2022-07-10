
from django.utils.decorators import sync_and_async_middleware
import logging
from django.conf import settings
from django.core.exceptions import  PermissionDenied
from django.contrib.auth import get_user_model
from ...bouncer.bounced import Bounced, get_session_app
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

UserModel = get_user_model()



class BouncerChannelMiddleware:
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
        if "auth" in scope:
            bounced =  Bounced.from_auth(scope["auth"])
            scope["bounced"]= bounced
            scope["user"] = bounced.user
            scope["app"] = bounced.app
        elif "session" in scope:
            app = await sync_to_async(get_session_app)()
            bounced = Bounced.from_session_app_and_user(app, scope["user"])
            scope["bounced"] = bounced
            scope["app"] = bounced.app
        else:
            scope["bounced"] = None
        return await self.app(scope, receive, send)