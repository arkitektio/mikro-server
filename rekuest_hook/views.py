"""A HookAgent's endpoints: its inbox — rekuest POSTs Assigns there; results go back to rekuest's
intake — and its manifest.

Mount an agent's endpoints (they honour the ``MY_SCRIPT_NAME`` prefix, like kante's
``dynamicpath``)::

    urlpatterns = [..., *agent.urls]          # a rekuest_hook.HookAgent

Keep ``/<prefix>/_rekuest/`` off the public edge (the Caddyfile answers it with 404): only
rekuest, on the internal network, has a reason to call it — though every request is signed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import traceback
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rekuest_service import trust

from rekuest_hook.agent import Action, HookAgent

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


#: Which of the hub's agents a delivery is for — every organization has its own, and this is
#: where the run is reported back (``agi/http/<agent>``). Routing, not security: the signed JWT
#: is what proves rekuest sent the request.
AGENT_HEADER = "X-Rekuest-Agent"


def _verified(agent: HookAgent, request: HttpRequest, config: dict[str, Any]) -> str | None:
    """The hub's agent id of a request rekuest really sent to THIS agent, or None."""
    try:
        verified = trust.verify(request.method or "", request.path, request.body, request.headers.get("Authorization"), audience=agent.signing_identifier(), max_skew=int(config.get("MAX_SKEW", 30)))
    except trust.TrustError as error:
        logger.info("Refused a request to %s: %s", request.path, error)
        return None
    if verified.issuer != agent.rekuest_identifier():
        logger.info("Refused a request to %s from %s: only rekuest may call it", request.path, verified.issuer)
        return None
    return request.headers.get(AGENT_HEADER, "")


def manifest(agent: HookAgent, request: HttpRequest) -> HttpResponse:
    """The agent's actions. Signed like an Assign."""
    config = agent.config()
    if config is None:
        return JsonResponse({"error": "rekuest_hook is not configured"}, status=503)
    if request.method != "GET":
        return HttpResponse(status=405)
    if _verified(agent, request, config) is None:
        return JsonResponse({"error": "Invalid signature"}, status=401)
    return JsonResponse(agent.manifest())


def hook(agent: HookAgent, request: HttpRequest) -> HttpResponse:
    """Receive one message from rekuest. An Assign is acknowledged at once and run in a thread."""
    config = agent.config()
    if config is None:
        return JsonResponse({"error": "rekuest_hook is not configured"}, status=503)
    if request.method != "POST":
        return HttpResponse(status=405)
    agent_id = _verified(agent, request, config)
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
    target = agent.actions.get(message.get("interface", ""))
    if target is None:
        _report(agent, config, agent_id, {"type": "CRITICAL", "task": task, "error": f"No action {message.get('interface')!r} on this agent"})
        return JsonResponse({"error": "Unknown interface"}, status=404)

    # A thread, not an event-loop task: it outlives the request under any server (WSGI
    # runserver tears its loop down with the response), and the function may block.
    args = dict(message.get("args") or {})
    if target.takes_organization:
        # Whose run this is: the organization of the agent rekuest assigned (every organization has its own).
        args["organization"] = message.get("org") or None
    threading.Thread(target=_run, args=(agent, config, agent_id, task, target, args), name=f"rekuest-hook-{task}", daemon=True).start()
    return JsonResponse({"accepted": task}, status=202)


def _run(agent: HookAgent, config: dict[str, Any], agent_id: str, task: str, target: Action, args: dict[str, Any]) -> None:
    """Run one Assign and report it. Started first: that is what tells rekuest it was picked up."""
    try:
        _report(agent, config, agent_id, {"type": "STARTED", "task": task})
        result = target.function(**args)
        if inspect.isawaitable(result):
            result = asyncio.run(_await(result))
        returns = result if isinstance(result, dict) else ({} if result is None else {"result": result})
        _report(agent, config, agent_id, {"type": "YIELD", "task": task, "returns": returns})
        _report(agent, config, agent_id, {"type": "COMPLETED", "task": task})
    except Exception as error:
        logger.warning("rekuest action %s (task %s) failed: %s", target.interface, task, error)
        logger.debug("rekuest action %s failed", target.interface, exc_info=True)
        _report(agent, config, agent_id, {"type": "CRITICAL", "task": task, "error": f"{type(error).__name__}: {error}\n{traceback.format_exc(limit=5)}"})
    finally:
        close_old_connections()


async def _await(awaitable):
    return await awaitable


def _report(agent: HookAgent, config: dict[str, Any], agent_id: str, message: dict[str, Any]) -> bool:
    """POST one signed event to rekuest's intake. Never raises: a lost report is rekuest's to time out."""
    body = json.dumps({"id": str(uuid.uuid4()), **message}).encode()
    url = f"{config['REKUEST_URL'].rstrip('/')}/agi/http/{agent_id}"
    try:
        authorization = trust.sign("POST", urlparse(url).path, body, issuer=agent.signing_identifier(), audience=agent.rekuest_identifier(), key=agent.signing_key())
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


def _prefixed(route: str) -> str:
    """``route`` under the process's ``MY_SCRIPT_NAME`` prefix, as kante's ``dynamicpath`` does."""
    prefix = (getattr(settings, "MY_SCRIPT_NAME", "") or "").strip("/")
    return f"{prefix}/{route}" if prefix else route


def urlpatterns_for(agent: HookAgent) -> list:
    """The two endpoints, bound to ``agent`` (what ``HookAgent.urls`` returns)."""

    @csrf_exempt
    def hook_view(request: HttpRequest) -> HttpResponse:
        return hook(agent, request)

    @csrf_exempt
    def manifest_view(request: HttpRequest) -> HttpResponse:
        return manifest(agent, request)

    return [
        path(_prefixed("_rekuest/hook"), hook_view, name="rekuest_hook"),
        path(_prefixed("_rekuest/hook/manifest"), manifest_view, name="rekuest_hook_manifest"),
    ]
