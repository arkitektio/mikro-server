"""What a service tells a hub about itself: what it needs, what it offers, what it runs beside.

Printed by ``describe``, before the service has any config. An installer reads it instead of
knowing the service: which buckets to make, whether to mint it a key, what to tell the
coordination server about it, which of its endpoints other services are wired to.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Said(BaseModel):
    """A closed block of a description."""

    model_config = ConfigDict(extra="forbid")


class Scope(Said):
    """A permission the service asks the coordination server to define."""

    key: str
    description: str


class Needs(Said):
    """What the service needs a hub to provide."""

    database: bool = True
    redis: bool = True
    storage: list[str] = Field(default_factory=list, description="A bucket for each of these purposes (media, zarr, parquet, bigfile, …).")
    instance_key: bool = Field(default=False, description="A key of its own, vouched for by the hub: it signs or verifies requests between services.")
    admin: bool = Field(default=True, description="It creates an operator account from the hub's admin.")
    peers: list[str] = Field(default_factory=list, description="Parts of the hub it uses when they are there (rekuest, ollama, livekit).")
    secrets: list[str] = Field(default_factory=list, description="Key files it needs mounted, by name (fernet).")
    scopes: list[Scope] = Field(default_factory=list)
    roles: list[Scope] = Field(default_factory=list)


class Offers(Said):
    """What the service offers a hub."""

    health: str = Field(default="ht", description="The path, under the service's own, that answers whether it works.")
    endpoints: dict[str, str] = Field(default_factory=dict, description="Endpoints other services are wired to, by kind, as paths under the service's own (rekuest_service: _rekuest/service).")


class Description(Said):
    """A service, as its image describes it."""

    contract: Literal[1] = Field(default=1, description="The version of this contract.")
    name: str = Field(description="The service's name: what a hub calls it by default.")
    summary: str = ""
    needs: Needs = Field(default_factory=Needs)
    offers: Offers = Field(default_factory=Offers)
    requires: dict[str, str] = Field(default_factory=dict, description="Peers this release only works beside in certain versions, as version specifiers (rekuest: '>=6').")
    upgrade_from: str | None = Field(default=None, description="The oldest version a deployment can be moved to this release from directly. Older ones have to stop at a release in between.")
