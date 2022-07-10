from ..models import LokUser, LokApp
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.http.response import HttpResponseBadRequest
from asgiref.sync import async_to_sync, sync_to_async
from ..token import JwtToken
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import Group
import logging

logger = logging.getLogger(__name__)

import uuid


def update_or_create_herre(decoded):
    if "email" in decoded and decoded["email"] is not None:
        try:
            user = get_user_model().objects.get(email=decoded["email"])
            if hasattr(user, "roles"):
                if user.roles != decoded["roles"]:
                    user.roles = decoded["roles"]
                    user.save()
                    groups = [
                        Group.objects.get_or_create(name=role)
                        for role in decoded["roles"]
                    ]
                    user.groups.set([g[0] for g in groups])
                    user.save()

        except ObjectDoesNotExist:
            user = get_user_model()(
                email=decoded["email"], username=decoded["preferred_username"]
            )
            user.set_unusable_password()
            user.save()
            if hasattr(user, "roles"):
                user.roles = decoded["roles"]
                groups = [
                    Group.objects.get_or_create(name=role) for role in decoded["roles"]
                ]
                user.groups.set([g[0] for g in groups])
                user.save()
            logger.warning("Created new ffff")
    else:
        user = None

    if "client_id" in decoded and decoded["client_id"] is not None:
        try:
            app = LokApp.objects.get(client_id=decoded["client_id"])
        except ObjectDoesNotExist:
            app = LokApp(
                client_id=decoded["client_id"],
                name=decoded["client_app"],
                grant_type=decoded["type"],
            )
            app.save()
            logger.warning("Created new app")
    else:
        app = None

    return user, app


@sync_to_async
def set_request_async(request, decoded, token):
    user, app = update_or_create_herre(decoded)
    request.auth = JwtToken(decoded, user, app, token)
    request.user = user
    return request


def set_request_sync(request, decoded, token):
    user, app = update_or_create_herre(decoded)
    request.auth = JwtToken(decoded, user, app, token)
    request.user = user
    return request


@sync_to_async
def set_scope_async(scope, decoded, token):
    user, app = update_or_create_herre(decoded)
    scope["auth"] = JwtToken(decoded, user, app, token)
    scope["user"] = user
    return scope
