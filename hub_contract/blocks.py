"""The config blocks most services share, written from a hub's facts.

Every service of a hub is a Django server on the same Postgres, redis and object store, and
spells those four blocks the same way. They are here so that a service's ``render`` says only
what is its own — and they are vendored with the service, so a release that spells one of
them differently changes its own copy and nobody else's.
"""

from __future__ import annotations

from hub_contract.contract import Refused
from hub_contract.facts import Facts
from hub_contract.json_types import JSON

#: The offer a hub's rekuest makes to the services that report to it: the agents' endpoint.
AGENT = "agent"


def django(facts: Facts) -> dict[str, JSON]:
    """``django``: the server's own settings."""
    admin = facts.me.admin
    return {
        "admin": {"username": admin.username, "password": admin.password, "email": admin.email} if admin else None,
        "csrf_trusted_origins": [*facts.hub.origins],
        "debug": facts.me.debug,
        "force_script_name": facts.me.path,
        "hosts": [*facts.me.allowed_hosts],
        "secret_key": facts.me.secret_key,
    }


def postgres(facts: Facts) -> dict[str, JSON]:
    """``postgres``: the service's database."""
    if facts.database is None:
        raise Refused("it needs a database, and this hub gives it none")
    database = facts.database
    return {"host": database.host, "port": database.port, "db_name": database.name, "username": database.username, "password": database.password}


def redis(facts: Facts) -> dict[str, JSON]:
    """``redis``: the hub's redis."""
    if facts.redis is None:
        raise Refused("it needs a redis, and this hub gives it none")
    return {"host": facts.redis.host, "port": facts.redis.port}


def datalayer(facts: Facts, *required: str) -> dict[str, JSON]:
    """``datalayer``: the object store, and a ``<purpose>: {bucket: …}`` entry per bucket made for the service."""
    if facts.storage is None:
        raise Refused("it needs object storage, and this hub gives it none")
    storage = facts.storage
    missing = [purpose for purpose in required if purpose not in storage.buckets]
    if missing:
        raise Refused(f"it needs a bucket for {', '.join(missing)}, and this hub made none")
    return {
        "access_key": storage.access_key,
        "secret_key": storage.secret_key,
        "host": storage.host,
        "port": storage.port,
        "protocol": storage.protocol,
        "region": storage.region,
        **({"role_arn": storage.role_arn} if storage.role_arn else {}),
        **({"session_duration_seconds": storage.session_duration_seconds} if storage.session_duration_seconds else {}),
        **{purpose: {"bucket": bucket} for purpose, bucket in storage.buckets.items()},
    }


def instance(facts: Facts) -> dict[str, JSON]:
    """``instance``: the service's own key, and whom it trusts."""
    if facts.instance is None:
        raise Refused("it needs an instance key, and this hub gives it none")
    return {"private_key": facts.instance.private_key, "trust": facts.instance.trust.model_dump(exclude_none=True)}


def rekuest_hook(facts: Facts) -> dict[str, JSON] | None:
    """``rekuest_hook``: where the service reports to, when the hub runs a rekuest."""
    for peer in facts.offering(AGENT).values():
        return {"rekuest_url": peer.offers[AGENT]}
    return None


def server(facts: Facts) -> dict[str, JSON]:
    """The four blocks every service has: ``django``, ``postgres``, ``redis``, ``authentikate``."""
    return {"django": django(facts), "postgres": postgres(facts), "redis": redis(facts), "authentikate": facts.hub.auth}
