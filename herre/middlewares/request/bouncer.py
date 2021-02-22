import asyncio
from django.utils.decorators import sync_and_async_middleware
from herre.bouncer.bounced import Bounced




@sync_and_async_middleware
def BouncedMiddleware(get_response):
    # One-time configuration and initialization goes here.
    if asyncio.iscoroutinefunction(get_response):
        async def middleware(request):
            # Do something here!
            if hasattr(request, "auth"):
                request.bounced = Bounced.from_auth(request.auth)
            elif hasattr(request, "session"):
                request.bounced = Bounced.from_session_and_user(request.session, request.user)
            else:
                request.bounced = None
            response = await get_response(request)
            return response

    else:
        def middleware(request):
            # Do something here!
            if hasattr(request, "auth"):
                request.bounced = Bounced(request.auth)
            elif hasattr(request, "session"):
                request.bounced = Bounced.from_session_and_user(request.session, request.user)
            else:
                request.bounced = None
            response = get_response(request)
            return response

    return middleware