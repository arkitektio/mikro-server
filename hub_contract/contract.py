"""A service's declaration: what it says of itself, and how it writes its config."""

from __future__ import annotations

import dataclasses
import importlib
import os
from collections.abc import Callable

from pydantic_settings import BaseSettings

from hub_contract.description import Description
from hub_contract.facts import Facts
from hub_contract.json_types import JSON

#: Names the module holding a service's ``contract``, in its image.
ENVIRONMENT = "HUB_CONTRACT"


class Refused(Exception):
    """A hub's facts this release cannot be configured from, said in words an operator reads.

    Raised by a service's ``render`` for what no schema says: a peer it cannot run without, a
    bucket it was not given.
    """


@dataclasses.dataclass(frozen=True)
class Contract:
    """One service, as its image declares it."""

    description: Description
    settings: type[BaseSettings]
    """The service's settings: what a rendered config is read by, and judged against."""
    render: Callable[[Facts], dict[str, JSON]]
    """This release's config, from a hub's facts."""
    upgrades: bool = False
    """Whether the release ships ``manage.py upgrade``."""


def load() -> Contract:
    """The contract of the service this image is, as ``HUB_CONTRACT`` names its module."""
    module = os.environ.get(ENVIRONMENT)
    if not module:
        raise LookupError(f"{ENVIRONMENT} is not set: this image does not say where its contract is")
    declared: object = getattr(importlib.import_module(module), "contract", None)
    if not isinstance(declared, Contract):
        raise TypeError(f"{module} has no `contract`")
    return declared
