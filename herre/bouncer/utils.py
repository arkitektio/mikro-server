
import asyncio

def bounce_info(info, required_roles=[], required_scopes=[], allowed_types=["m2m","u2m"], anonymous=False):
    context = info.context
    if hasattr(context, "bounced"):
        # We are probably dealing with a normal request
        assert context.bounced is not None, "No bounced context provided"
        bouncer = context.bounced
    elif hasattr(context, "_scope"):
        scope = context._scope
        assert "bounced" in scope, "Bounced not in context, did you install the middleware?"
        assert scope["bounced"] is not None, "No bounced context provided"
        bouncer = scope["bounced"]

    bouncer.bounce(required_roles=required_roles, required_scopes=required_scopes, allowed_types=allowed_types, anonymous=anonymous)
    setattr(context, "user", bouncer.user)
    return info



def bounced(required_roles=[], required_scopes=[], allowed_types=["m2m","u2m"], anonymous=False):


    def real_decorator(function):

        if asyncio.iscoroutinefunction(function):
            async def bounced_function(root, info, *args, **kwargs):
                info = bounce_info(info, required_roles=required_roles, required_scopes=required_scopes, allowed_types=allowed_types, anonymous=anonymous)
                return await function(root, info, *args, **kwargs)

        else:
            def bounced_function(root, info, *args, **kwargs):
                info = bounce_info(info, required_roles=required_roles, required_scopes=required_scopes, allowed_types=allowed_types, anonymous=anonymous)
                return function(root, info, *args, **kwargs)


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

