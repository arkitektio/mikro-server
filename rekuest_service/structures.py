"""What a service hosts: its structures, and the descriptors of each.

A structure is a model the service holds, known to the hub by its identifier
(``@mikro/arraydataset``). Its declaration is the one place that says which descriptors the
objects carry (``descriptors``) and how to compute them (``describe``): the manifest rekuest
reads, the service's own ``descriptors`` GraphQL field and any signal declared for the structure
all read it.

A structure only HAS descriptors, and declaring one announces nothing: what is signalled about it
is a separate declaration (``Service.model_signal``). What an action requires of an input or
provides with an output is said on that action's ports, in terms of these keys.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

IDENTIFIER = re.compile(r"^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: What a descriptor's value is; ``ANY`` when the service does not say.
DESCRIPTOR_TYPES = ("ANY", "INT", "FLOAT", "STRING", "BOOL", "LIST")


def check_identifier(identifier: str) -> str:
    if not IDENTIFIER.match(identifier):
        raise ValueError(f"A structure identifier looks like @package/key, not {identifier!r}")
    return identifier


@dataclass(frozen=True)
class Descriptor:
    """One descriptor of a structure's objects, e.g. ``Descriptor("@mikro/n_channels", "INT")``."""

    key: str
    type: str = "ANY"
    description: str | None = None

    def __post_init__(self) -> None:
        if self.type not in DESCRIPTOR_TYPES:
            raise ValueError(f"A descriptor type is one of {DESCRIPTOR_TYPES}, not {self.type!r}")

    def manifest(self) -> dict[str, Any]:
        return {"key": self.key, "type": self.type, "description": self.description}


@dataclass(frozen=True)
class Structure:
    """A hosted structure: a model, its identifier, and the descriptors of its objects."""

    identifier: str
    model: Any
    label: str | None
    description: str | None
    descriptors: tuple[Descriptor, ...]
    describer: Callable[[Any], dict[str, Any]] | None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.descriptors)

    def describe(self, obj: Any) -> dict[str, Any]:
        """The flat descriptor dict of ``obj`` — what signals for it carry and its ``descriptors`` field returns."""
        return dict(self.describer(obj)) if self.describer is not None else {}

    def manifest(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "label": self.label, "description": self.description, "descriptors": [d.manifest() for d in self.descriptors]}
