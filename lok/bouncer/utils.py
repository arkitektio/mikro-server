
import asyncio
from ..bouncer.bounced import BounceException

import logging
logger = logging.getLogger(__name__)

def bounce(context_or_scope, required_roles=[], required_scopes=[], allowed_types=["m2m","u2m"], anonymous=False, only_jwt=False):
    
    if hasattr(context_or_scope, "bounced"): # is 
        # We are probably dealing with a normal request
        assert context_or_scope.bounced is not None, "No bounced context provided"
        bouncer = context_or_scope.bounced


        bouncer.bounce(required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
        setattr(context_or_scope, "user", bouncer.user)

    elif hasattr(context_or_scope, "_scope"):
        scope = context_or_scope._scope
        assert "bounced" in scope, "Bounced not in context, did you install the middleware?"
        assert scope["bounced"] is not None, "No bounced context provided"
        bouncer = scope["bounced"]

        bouncer.bounce(required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
        setattr(context_or_scope, "user", bouncer.user)


    elif "bounced" in context_or_scope: # We are dealing with a scobe (websocket request)
        bouncer = context_or_scope["bounced"]

        bouncer.bounce(required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
        context_or_scope["user"] = bouncer.user
    else:
        raise Exception("Unknown Request")

    return context_or_scope



def bounced(required_roles=[], required_scopes=[], allowed_types=["m2m","u2m"], anonymous=False, only_jwt=False):


    def real_decorator(function):

        if asyncio.iscoroutinefunction(function):
            async def bounced_function(root, info, *args, **kwargs):
                bounce(info.context, required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
                return await function(root, info, *args, **kwargs)

        else:
            def bounced_function(root, info, *args, **kwargs):
                bounce(info.context, required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
                return function(root, info, *args, **kwargs)


        return bounced_function


    return real_decorator



def bounced_ws(required_roles=[], required_scopes=[], allowed_types=["m2m","u2m"], anonymous=False, only_jwt=False):


    def real_decorator(function):

        if asyncio.iscoroutinefunction(function):
            async def bounced_function(self, *args, **kwargs):
                try:
                    bounce(self.scope, required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
                except BounceException as e:
                    logger.error(f"Closed because of bouncing {e}")
                    await self.close(e)
                
                return await function(self, *args, **kwargs)

        else:
            def bounced_function(self, *args, **kwargs):
                try:
                   bounce(self.scope, required_roles=required_roles, required_scopes=required_scopes, anonymous=anonymous, only_jwt=only_jwt)
                except BounceException as e:
                    logger.error(f"Closed because of bouncing {e}")
                    self.close(e)
                return function(self, *args, **kwargs)


        return bounced_function


    return real_decorator


def bounced_request(required_roles=[], required_scopes=[]):


    def real_decorator(function):

        if asyncio.iscoroutinefunction(function):
            async def bounced_function(request, *args, **kwargs):
                assert hasattr(request, "bounced"), "Bounced not in context, did you install the middleware?"
                assert request.bounced is not None, "No bounced context provided"
                request.bounced.bounce(required_roles=required_roles, required_scopes=required_scopes)
                return await function(request, *args, **kwargs)

        else:
            def bounced_function(request, *args, **kwargs):
                assert hasattr(request, "bounced"), "Bounced not in context, did you install the middleware?"
                assert request.bounced is not None, "No bounced context provided"
                request.bounced.bounce(required_roles=required_roles,required_scopes=required_scopes)
                return function(request, *args, **kwargs)


        return bounced_function


    return real_decorator

