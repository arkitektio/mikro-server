"""A port's ``requires``, asked of the rows in SQL: which objects satisfy these constraints.

The other direction of ``core.descriptors``. A describer answers what one object is; this
answers which objects are something, so a picker for a port typed ``Volume`` offers the
volumes and pages through them without a describer run per row.

A constraint is ``{key, operator, value}``, the shape a rekuest port states it in, and the
operators mean what ``arkitekt_spec.declare.descriptors.holds`` says they mean (``MATCHES`` is
equality). Every constraint must hold.

Every descriptor is stored on its row (``stored_descriptors``), so every constraint is a
condition on that column, served by its ``jsonb_path_ops`` index: equality is containment, an
ordering compares one member (``arkitekt_service.service.requires``). A lens kind's keys are
asked of the base ``Lens``: a row of a kind that does not state a key does not hold it.

A key the structure does not declare is refused, never ignored: a filter that silently drops
a constraint offers objects the port will then refuse.
"""

import json
import re
from collections.abc import Sequence
from enum import Enum

import strawberry
from django.db.models import BooleanField, F, Func, JSONField, Q, QuerySet, TextField, Value
from strawberry.scalars import JSON


@strawberry.enum(description="How a descriptor constraint compares the object's value with the stated one. The operators a rekuest port's `requires` and `provides` are written in, with the same meaning.")
class DescriptorOperator(str, Enum):
    """The comparison of one constraint."""

    EQUALS = "EQUALS"
    MATCHES = "MATCHES"
    NOT_EQUALS = "NOT_EQUALS"
    GTE = "GTE"
    LTE = "LTE"
    EXISTS = "EXISTS"
    CONTAINS = "CONTAINS"
    IN = "IN"
    NOT_IN = "NOT_IN"


@strawberry.input(
    description=(
        "One constraint on an object's descriptors, in the shape a rekuest port states its `requires` in -- so a picker hands a port's constraints on unchanged. "
        "`key` is one of the keys the structure declares (e.g. `@mikro/n_space_axes`); a key it does not declare is refused."
    )
)
class DescriptorConstraintInput:
    """One ``{key, operator, value}`` constraint."""

    key: str = strawberry.field(description="The descriptor key, e.g. `@mikro/n_space_axes`")
    operator: DescriptorOperator = strawberry.field(description="How the object's value is compared with `value`. MATCHES is equality, as EQUALS is")
    value: JSON | None = strawberry.field(default=None, description="The value compared against: a number for GTE and LTE, a list for IN and NOT_IN, true or false for EXISTS, the member looked for with CONTAINS")


def requires(model: type, queryset: QuerySet, constraints: Sequence[DescriptorConstraintInput], prefix: str = "") -> tuple[QuerySet, Q]:
    """The rows of ``model`` satisfying every one of these constraints. No constraints ask nothing."""
    from mikro_server.service import service  # it binds the models this module filters

    return queryset, service.requires(model, constraints, prefix)


@strawberry.input(
    description=(
        "What one implementation can handle, as the rekuest server compiled its `Fits`: objects satisfying it are ones that implementation's agent can take. "
        "`path` comes from `Implementation.fits.compiledPath`, and only comparisons of numbers over declared descriptors and `$agent` are accepted."
    )
)
class FitsConstraintInput:
    """One compiled ``Fits`` and the hardware of its agent."""

    path: str = strawberry.field(description="The formula as a PostgreSQL jsonpath.")
    agent: JSON | None = strawberry.field(default=None, description="The agent's hardware the path reads as `$agent`: numbers by name.")


_MAX_PATH_LENGTH = 2000
_MAX_FITS = 64
_AGENT_HARDWARE = frozenset({"cpu_count", "memory_mib", "gpu_count", "gpu_memory_mib"})
_FITS_TOKEN = re.compile(
    r"""\s*(?:
        (?P<key>\$\."(?:[^"\\]|\\.)*")
      | \$agent\.(?P<hardware>[a-z_]+)
      | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
      | (?P<operator><=|>=|&&|\|\||[-+*/()<>])
      | (?P<method>\.(?:abs|floor|ceiling)\(\))
    )\s*""",
    re.VERBOSE,
)


def _check_fits_path(path: str, declared: set[str]) -> None:
    """Refuse anything but what the rekuest server's compiler writes.

    The path comes from a client and runs here, so it is held to a whitelist: a length cap alone
    would let through ``like_regex``, ``.**``, ``.datetime()`` and the rest of jsonpath.
    """
    if len(path) > _MAX_PATH_LENGTH:
        raise ValueError(f"A fits path is at most {_MAX_PATH_LENGTH} characters.")
    position = 0
    while position < len(path):
        token = _FITS_TOKEN.match(path, position)
        if token is None:
            raise ValueError(f"A fits path only compares numbers: refused at {path[position:position + 20]!r}.")
        if token["key"] is not None and json.loads(token["key"][2:]) not in declared:
            raise ValueError(f"{json.loads(token['key'][2:])!r} is not a descriptor here: the structure declares {sorted(declared)}.")
        if token["hardware"] is not None and token["hardware"] not in _AGENT_HARDWARE:
            raise ValueError(f"$agent.{token['hardware']} is not a hardware value: {sorted(_AGENT_HARDWARE)}.")
        position = token.end()


def _agent_vars(agent: JSON | None) -> dict:
    hardware = agent or {}
    if not isinstance(hardware, dict) or any(key not in _AGENT_HARDWARE or (value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)))) for key, value in hardware.items()):
        raise ValueError(f"$agent is numbers by name: {sorted(_AGENT_HARDWARE)}.")
    return {"agent": hardware}


def fits(model: type, queryset: QuerySet, constraints: Sequence[FitsConstraintInput], prefix: str = "") -> tuple[QuerySet, Q]:
    """The rows at least one of these implementations can handle. No constraints ask nothing."""
    from mikro_server.service import service  # it binds the models this module filters

    if not constraints:
        return queryset, Q()
    if len(constraints) > _MAX_FITS:
        raise ValueError(f"At most {_MAX_FITS} fits constraints.")
    declared = {key for structure in service.structures.values() if isinstance(structure.model, type) and issubclass(structure.model, model) for key in structure.keys}
    condition = Q()
    for constraint in constraints:
        _check_fits_path(constraint.path, declared)
        condition |= Q(
            Func(
                F(f"{prefix}stored_descriptors"),
                Func(Value(constraint.path), template="%(expressions)s::jsonpath", output_field=TextField()),
                Value(_agent_vars(constraint.agent), output_field=JSONField()),
                Value(True),
                function="jsonb_path_match",
                output_field=BooleanField(),
            )
        )
    return queryset, condition
