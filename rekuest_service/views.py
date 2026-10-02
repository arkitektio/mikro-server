"""The HookAgent endpoint: rekuest POSTs Assigns here; results go back to rekuest's intake.

Mount a service's endpoints (they honour the ``MY_SCRIPT_NAME`` prefix, like kante's
``dynamicpath``)::

    urlpatterns = [..., *service.urls]          # a rekuest_service.Service

(``rekuest_service.views.urlpatterns`` serves the module-level default service, for existing callers.)

Keep ``/<prefix>/_rekuest/`` off the public edge (the Caddyfile answers it with 404): only
rekuest, on the internal network, has a reason to call it — though every request is signed.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import threading
import traceback
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from rekuest_service import trust
from rekuest_service.service import Action, Service, default_service

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


#: The HookAgent a delivery is for — where the service reports back (``agi/http/<agent>``).
#: Routing, not security: the service JWT is what proves rekuest sent the request.
AGENT_HEADER = "X-Rekuest-Agent"


def _verified(service: Service, request: HttpRequest, config: dict[str, Any]) -> str | None:
    """The agent id of a request rekuest really sent to THIS service, or None."""
    audience = service.signing_identifier()
    if not audience:
        return None
    try:
        verified = trust.verify(request.method or "", request.path, request.body, request.headers.get("Authorization"), audience=audience, max_skew=int(config.get("MAX_SKEW", 30)))
    except trust.TrustError as error:
        logger.info("Refused a request to %s: %s", request.path, error)
        return None
    if verified.issuer != service.rekuest_identifier():
        logger.info("Refused a request to %s from %s: only rekuest may call it", request.path, verified.issuer)
        return None
    return request.headers.get(AGENT_HEADER, "")


def manifest(service: Service, request: HttpRequest) -> HttpResponse:
    """The actions this service offers (with their default schedules) and the signals it emits. Signed like an Assign."""
    config = service.config()
    if config is None:
        return JsonResponse({"error": "rekuest_hook is not configured"}, status=503)
    if request.method != "GET":
        return HttpResponse(status=405)
    if _verified(service, request, config) is None:
        return JsonResponse({"error": "Invalid signature"}, status=401)
    return JsonResponse(service.manifest())


def hook(service: Service, request: HttpRequest) -> HttpResponse:
    """Receive one message from rekuest. An Assign is acknowledged at once and run in a thread."""
    config = service.config()
    if config is None:
        return JsonResponse({"error": "rekuest_hook is not configured"}, status=503)
    if request.method != "POST":
        return HttpResponse(status=405)
    agent_id = _verified(service, request, config)
    if not agent_id:
        return JsonResponse({"error": "Invalid signature"}, status=401)
    try:
        message = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Not JSON"}, status=400)

    if message.get("type") != "ASSIGN":
        # Cancel / Interrupt / Pause of a short housekeeping run: nothing to wind down here —
        # rekuest's control deadline settles it. Anything else is not ours to act on.
        return JsonResponse({"ignored": message.get("type")})

    task = str(message.get("task", ""))
    target = service.actions.get(message.get("interface", ""))
    if target is None:
        _report(service, config, agent_id, {"type": "CRITICAL", "task": task, "error": f"No action {message.get('interface')!r} on this service"})
        return JsonResponse({"error": "Unknown interface"}, status=404)

    # A thread, not an event-loop task: it outlives the request under any server (WSGI
    # runserver tears its loop down with the response), and the function may block.
    threading.Thread(target=_run, args=(service, config, agent_id, task, target, message.get("args") or {}), name=f"rekuest-hook-{task}", daemon=True).start()
    return JsonResponse({"accepted": task}, status=202)


def _run(service: Service, config: dict[str, Any], agent_id: str, task: str, target: Action, args: dict[str, Any]) -> None:
    """Run one Assign and report it. Started first: that is what tells rekuest it was picked up."""
    try:
        _report(service, config, agent_id, {"type": "STARTED", "task": task})
        result = target.function(**args)
        if inspect.isawaitable(result):
            result = asyncio.run(_await(result))
        returns = result if isinstance(result, dict) else ({} if result is None else {"result": result})
        _report(service, config, agent_id, {"type": "YIELD", "task": task, "returns": returns})
        _report(service, config, agent_id, {"type": "COMPLETED", "task": task})
    except Exception as error:
        logger.warning("rekuest action %s (task %s) failed: %s", target.interface, task, error)
        logger.debug("rekuest action %s failed", target.interface, exc_info=True)
        _report(service, config, agent_id, {"type": "CRITICAL", "task": task, "error": f"{type(error).__name__}: {error}\n{traceback.format_exc(limit=5)}"})
    finally:
        close_old_connections()


async def _await(awaitable):
    return await awaitable


def _report(service: Service, config: dict[str, Any], agent_id: str, message: dict[str, Any]) -> bool:
    """POST one signed event to rekuest's intake. Never raises: a lost report is rekuest's to time out."""
    body = json.dumps({"id": str(uuid.uuid4()), **message}).encode()
    url = f"{config['REKUEST_URL'].rstrip('/')}/agi/http/{agent_id}"
    try:
        authorization = trust.sign("POST", urlparse(url).path, body, issuer=service.signing_identifier() or "", audience=service.rekuest_identifier(), key=service.signing_key())
    except trust.TrustError as error:
        logger.warning("Could not sign a report for task %s: %s", message.get("task"), error)
        return False
    headers = {"Content-Type": "application/json", "Authorization": authorization}
    try:
        response = httpx.post(url, content=body, headers=headers, timeout=_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as error:  # noqa: BLE001  never raises: a lost delivery is rekuest's to time out
        logger.warning("Could not report %s for task %s to rekuest: %s", message.get("type"), message.get("task"), error)
        return False


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
    """The two endpoints, bound to ``service`` (what ``Service.urls`` returns)."""

    @csrf_exempt
    def hook_view(request: HttpRequest) -> HttpResponse:
        return hook(service, request)

    @csrf_exempt
    def manifest_view(request: HttpRequest) -> HttpResponse:
        return manifest(service, request)

    return [
        path(_prefixed("_rekuest/hook"), hook_view, name="rekuest_hook"),
        path(_prefixed("_rekuest/hook/manifest"), manifest_view, name="rekuest_hook_manifest"),
    ]


#: The default service's endpoints (``rekuest_service.action`` / ``declare_signal`` register there).
urlpatterns = urlpatterns_for(default_service)
