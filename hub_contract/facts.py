"""What a hub tells a service about itself: the only input a service's config is written from.

Written by the installer, one file per service, and read by that service's image (``render``).
Nothing here is in a service's own vocabulary — no ``datalayer``, no ``rekuest_hook`` — so that
how a release spells its config is the release's alone. The models are closed: a fact this
contract does not know is an installer newer than the image can serve, and is refused by name
rather than dropped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hub_contract.json_types import JSON


class Fact(BaseModel):
    """A closed block of facts."""

    model_config = ConfigDict(extra="forbid")


class Admin(Fact):
    """The account a service creates for the hub's operator."""

    username: str
    password: str
    email: str | None = None


class Me(Fact):
    """The service this file is for, as the hub runs it."""

    name: str = Field(description="The service's name in the hub: its compose service and its path.")
    path: str = Field(description="The path it is served under at the gateway, without slashes: its script name.")
    url: str = Field(description="Where it is reached from inside the hub, with its path.")
    identifier: str = Field(description="Its identifier at the coordination server, e.g. live.arkitekt.mikro.")
    secret_key: str = Field(description="A secret of its own (Django's SECRET_KEY).")
    debug: bool = False
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])
    admin: Admin | None = None
    settings: dict[str, str] = Field(default_factory=dict, description="What else the hub decided for this service, by name.")


class Hub(Fact):
    """What is the same for every service of the hub."""

    origins: list[str] = Field(default_factory=list, description="The origins the hub is reached at (CSRF trusted origins).")
    auth: dict[str, JSON] = Field(description="How tokens are verified here: authentikate's settings, the same for every service.")


class Database(Fact):
    """The service's own database in the hub's Postgres."""

    host: str
    port: int = 5432
    name: str
    username: str
    password: str


class Redis(Fact):
    """The hub's redis."""

    host: str
    port: int = 6379


class Storage(Fact):
    """The hub's object store, and the buckets made for this service."""

    host: str
    port: int
    protocol: str = "http"
    region: str = "us-east-1"
    access_key: str
    secret_key: str
    role_arn: str | None = None
    session_duration_seconds: int | None = None
    buckets: dict[str, str] = Field(default_factory=dict, description="The bucket made for each purpose the service asked for (media, zarr, …).")


class Trust(Fact):
    """Where the hub's instance public keys come from: the coordination server's bundle, or inline."""

    jwks_uri: str | None = None
    jwks: dict[str, JSON] | None = None


class Instance(Fact):
    """The service's own key, and whom it trusts."""

    private_key: str
    trust: Trust = Field(default_factory=Trust)


class Peer(Fact):
    """Another part of the hub this service may talk to."""

    identifier: str | None = Field(default=None, description="Its identifier in the trust bundle, for a service of the hub.")
    url: str = Field(description="Where it is reached from inside the hub, with its path.")
    offers: dict[str, str] = Field(default_factory=dict, description="The endpoints it offers, by kind, as full internal URLs (rekuest_service, rekuest_hook, agent, …).")
    settings: dict[str, str] = Field(default_factory=dict, description="What else one needs to use it (an API key, a socket path).")


class Facts(Fact):
    """Everything a hub tells one service."""

    facts: Literal[1] = Field(default=1, description="The version of this document.")
    me: Me
    hub: Hub
    database: Database | None = None
    redis: Redis | None = None
    storage: Storage | None = None
    instance: Instance | None = None
    peers: dict[str, Peer] = Field(default_factory=dict, description="The other parts of the hub, by name.")
    secrets: dict[str, str] = Field(default_factory=dict, description="Files mounted for the service, by name, as paths in its container.")

    def offering(self, kind: str) -> dict[str, Peer]:
        """The peers that offer ``kind``, by name."""
        return {name: peer for name, peer in self.peers.items() if kind in peer.offers}
