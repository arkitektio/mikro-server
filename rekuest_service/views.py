"""A service's endpoint towards rekuest: its manifest — and the signed health answer of an instance.

Mount a service's endpoint (it honours the ``MY_SCRIPT_NAME`` prefix, like kante's
``dynamicpath``)::

    urlpatterns = [..., *service.urls]          # a rekuest_service.Service

Keep ``/<prefix>/_rekuest/`` off the public edge (the Caddyfile answers it with 404): only
rekuest, on the internal network, has a reason to call it — though every request is signed.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from rekuest_service import trust
from rekuest_service.service import Service

logger = logging.getLogger(__name__)


def manifest(service: Service, request: HttpRequest) -> HttpResponse:
    """What this service hosts and emits. Signed like every request between rekuest and a service."""
    config = service.config()
    if config is None:
        return JsonResponse({"error": "rekuest_service is not configured"}, status=503)
    if request.method != "GET":
        return HttpResponse(status=405)
    try:
        verified = trust.verify("GET", request.path, request.body, request.headers.get("Authorization"), audience=service.signing_identifier(), max_skew=int(config.get("MAX_SKEW", 30)))
    except trust.TrustError as error:
        logger.info("Refused a request to %s: %s", request.path, error)
        return JsonResponse({"error": "Invalid signature"}, status=401)
    if verified.issuer != service.rekuest_identifier():
        logger.info("Refused a request to %s from %s: only rekuest may call it", request.path, verified.issuer)
        return JsonResponse({"error": "Invalid signature"}, status=401)
    return JsonResponse(service.manifest())


def _challenge_answer(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    """``response``, or the signed answer when the request is a fakts challenge of a healthy service."""
    nonce = request.GET.get("nonce")
    if request.method != "GET" or nonce is None or response.status_code != 200:
        return response
    try:
        signature = trust.sign_challenge(nonce)
    except trust.TrustError:
        return JsonResponse({"error": "Not a challenge nonce"}, status=400)
    if signature is None:
        return response
    return JsonResponse({"signature": signature})


def answers_challenge(view: Any) -> Any:
    """Make a health view answer fakts' signed alias challenge (wrap the ``ht`` route with it).

    ``GET ht?nonce=<nonce>`` is answered with ``{"signature": ...}`` (:func:`trust.sign_challenge`)
    — only when the health view itself answers 200, so a signature still means "up". Without a
    nonce, or without an instance key, the health view's own response passes through untouched.
    Wraps sync and async views alike::

        dynamicpath("ht", answers_challenge(csrf_exempt(MainView.as_view())), name="health_check")
    """
    if iscoroutinefunction(view):

        @functools.wraps(view)
        async def async_wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            return _challenge_answer(request, await view(request, *args, **kwargs))

        markcoroutinefunction(async_wrapped)
        return async_wrapped

    @functools.wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return _challenge_answer(request, view(request, *args, **kwargs))

    return wrapped


def _prefixed(route: str) -> str:
    """``route`` under the service's ``MY_SCRIPT_NAME`` prefix, as kante's ``dynamicpath`` does."""
    prefix = (getattr(settings, "MY_SCRIPT_NAME", "") or "").strip("/")
    return f"{prefix}/{route}" if prefix else route


def urlpatterns_for(service: Service) -> list:
    """The manifest endpoint, bound to ``service`` (what ``Service.urls`` returns)."""

    @csrf_exempt
    def manifest_view(request: HttpRequest) -> HttpResponse:
        return manifest(service, request)

    return [path(_prefixed("_rekuest/service/manifest"), manifest_view, name="rekuest_service_manifest")]
