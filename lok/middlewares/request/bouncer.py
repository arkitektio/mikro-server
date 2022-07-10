import asyncio
from asgiref.sync import sync_to_async
from django.utils.decorators import sync_and_async_middleware
from ...bouncer.bounced import Bounced, get_session_app




@sync_and_async_middleware
def BouncedMiddleware(get_response):
    # One-time configuration and initialization goes here.
    if asyncio.iscoroutinefunction(get_response):
        async def middleware(request):
            # Do something here!
            if hasattr(request, "auth"):
                bounced = Bounced.from_auth(request.auth)
                request.bounced = Bounced.from_auth(request.auth)
                setattr(request, "user", bounced.user)
            elif hasattr(request, "session"):
                app = await sync_to_async(get_session_app)()
                request.bounced = Bounced.from_session_app_and_user(app, request.user)
            else:
                request.bounced = None
            response = await get_response(request)
            return response

    else:
        def middleware(request):
            # Do something here!
            if hasattr(request, "auth"):
                bounced = Bounced.from_auth(request.auth)
                request.bounced = bounced
                setattr(request, "user", bounced.user)
            elif hasattr(request, "session"):
                app = get_session_app()
                request.bounced = Bounced.from_session_app_and_user(app, request.user)
            else:
                request.bounced = None
            response = get_response(request)
            return response

    return middleware