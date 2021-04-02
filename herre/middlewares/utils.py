
from herre.models import HerreUser
from django.core.exceptions import  PermissionDenied
from django.contrib.auth import get_user_model
from django.http.response import HttpResponseBadRequest
from asgiref.sync import async_to_sync, sync_to_async
from herre.token import JwtToken

import logging

logger = logging.getLogger(__name__)
UserModel = get_user_model()
import uuid


def update_or_create_herre(decoded):
    if "email" in decoded and decoded["email"] is not None:
        try:
            user = UserModel.objects.get(email=decoded["email"])
        except HerreUser.DoesNotExist:
            user = UserModel(email=decoded["email"])
            user.set_unusable_password()
            user.save()
            logger.warning("Created new user")
    else:
        user = None

    return user



@sync_to_async
def set_request_async(request, decoded, token):
    print(decoded)
    user = update_or_create_herre(decoded)
    request.auth = JwtToken(decoded, user, token)
    request.user = user
    return request

def set_request_sync(request, decoded, token):
    user = update_or_create_herre(decoded)
    request.auth = JwtToken(decoded, user, token)
    request.user = user
    return request


@sync_to_async
def set_scope_async(scope, decoded, token):
    user = update_or_create_herre(decoded)
    scope["auth"] = JwtToken(decoded, user, token)
    scope["user"] = user
    return scope
