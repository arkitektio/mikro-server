"""Signals on the wire — the service side of a rekuest Signal.

Declare and emit through a :class:`rekuest_service.Service`::

    dataset_created = service.signal("@mikro/arraydataset", kinds=["CREATED"], descriptors=[...])
    dataset_created.emit(dataset.pk, organization=dataset.organization.slug, descriptors={...})

rekuest matches the signal against its users' triggers and runs their actions on the object.
When this runs inside a request that carried a provenance token (the service was called in a
rekuest task), that token goes along untouched; rekuest verifies it against its own key and
makes the triggered runs children of that task. Nothing else is trusted: a service cannot name
a causing task it was not called in.

Best-effort, on purpose: the POST happens after the surrounding transaction commits (a rolled
back object is never announced), in a thread, and a failure is one warning line. Nothing here
retries, queues or loops. The module-level :func:`emit` sends through the default service.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any
from urllib.parse import urlparse

import httpx

from rekuest_service import trust

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def current_provenance_token() -> str | None:
    """The raw provenance token of the request being served, if koherent holds one."""
    try:
        from koherent.vars import get_current_provenance
    except ImportError:
        return None
    provenance = get_current_provenance()
    return getattr(provenance, "raw", None) if provenance is not None else None


def emit(kind: str, identifier: str, object: Any, *, organization: str, descriptors: dict[str, Any] | None = None) -> None:
    """Announce ``kind`` of ``identifier:object`` through the default service (kept for existing callers)."""
    from rekuest_service.service import KINDS, default_service

    if kind not in KINDS:
        raise ValueError(f"A signal kind is one of {KINDS}, not {kind!r}")
    handle = default_service.signals.get(identifier)
    if handle is not None and kind in handle.declaration.kinds:
        handle.emit(object, organization=organization, descriptors=descriptors, kind=kind)
    else:
        default_service._emit(kind, identifier, object, organization=organization, descriptors=descriptors)


_outbox: queue.Queue = queue.Queue(maxsize=10_000)
_sender: threading.Thread | None = None
_sender_lock = threading.Lock()


def enqueue(config: dict[str, Any], service: str, issuer: str, message: dict[str, Any], key: Any = None) -> None:
    """Hand a signal to this process's sender thread: one at a time, in commit order.

    One thread, not one per signal — a sync that touches a thousand rows must not start a
    thousand threads, and a CREATED must reach rekuest before the DELETED of the same row. A
    full outbox (rekuest unreachable for long) drops the signal with a warning: best-effort.
    """
    global _sender
    with _sender_lock:
        if _sender is None or not _sender.is_alive():
            _sender = threading.Thread(target=_drain, name="rekuest-signal-sender", daemon=True)
            _sender.start()
    try:
        _outbox.put_nowait((config, service, issuer, message, key))
    except queue.Full:
        logger.warning("Signal outbox full; dropping %s %s:%s", message["kind"], message["identifier"], message["object"])


def _drain() -> None:
    while True:
        config, service, issuer, message, key = _outbox.get()
        try:
            send(config, service, issuer, message, key)
        finally:
            _outbox.task_done()


def send(config: dict[str, Any], service: str, issuer: str, message: dict[str, Any], key: Any = None) -> bool:
    """POST one signal to rekuest's signal intake, signed with this instance's key. Never raises."""
    from rekuest_service.service import Service

    body = json.dumps(message).encode()
    url = f"{config['REKUEST_URL'].rstrip('/')}/agi/signal/{service}"
    try:
        authorization = trust.sign("POST", urlparse(url).path, body, issuer=issuer, audience=Service.rekuest_identifier(), key=key)
    except trust.TrustError as error:
        logger.warning("Could not sign a signal: %s", error)
        return False
    headers = {"Content-Type": "application/json", "Authorization": authorization}
    try:
        response = httpx.post(url, content=body, headers=headers, timeout=_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as error:  # noqa: BLE001  never raises: a lost delivery is rekuest's to time out
        logger.warning("Could not signal %s %s:%s to rekuest: %s", message["kind"], message["identifier"], message["object"], error)
        return False
