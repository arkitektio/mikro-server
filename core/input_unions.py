"""Discriminated input unions, announced to client codegen via ``@unionElementOf``.

GraphQL has no input unions, so a union that must arrive through an argument is
wired as one *flat* input: a discriminator field plus the union of every member's
fields, all optional. The flat type is honest on the wire but silent about its
structure, so each member is also published as a real input type that no field
references, annotated with this directive -- which union it belongs to, which
field discriminates, and which discriminator value selects it. A generated client
rebuilds the tagged union from those annotations. The convention, including the
directive's exact name and arguments, is shared with kabinet's ``SelectorInput``.

The server-side half is :func:`parse_union_member`: the flat model is matched to
its member model by the discriminator, and the member models forbid fields that
are not their own -- so a parameter that contradicts the discriminator is an
error naming both, never a silent drop. That strictness is this module's reason
to exist; kabinet's variant models accept and discard strays.

Nothing here is mikro-specific; the module is written to move into kante once the
convention settles.
"""

from collections.abc import Callable
from typing import TypeVar

import strawberry
from pydantic import BaseModel, ValidationError
from strawberry.schema_directive import Location

T = TypeVar("T")

#: pydantic prefixes the message of a `ValueError` raised inside a validator with this.
#: Stripped on the way out: the validator already wrote a full sentence, and the prefix
#: names pydantic's error machinery rather than anything the caller did.
_VALUE_ERROR_PREFIX = "Value error, "


@strawberry.schema_directive(locations=[Location.INPUT_OBJECT], repeatable=True)
class unionElementOf:  # the lowercase class name IS the SDL directive name, and kabinet already fixed it
    """Marks an input type as one member of a flat discriminated union input."""

    union: str = strawberry.field(description="The name of the flat input type this member belongs to")
    discriminator: str = strawberry.field(description="The field of the flat input whose value selects a member")
    key: str = strawberry.field(description="The discriminator value that selects this member")


def union_memberships(*unions: str, key: str, discriminator: str = "kind") -> list[unionElementOf]:
    """The directive instances declaring one member input's place in each named union."""
    return [unionElementOf(union=union, discriminator=discriminator, key=key) for union in unions]


def parse_union_member(
    members: dict[str, type[BaseModel]],
    data: dict,
    *,
    noun: str,
    discriminator: str = "kind",
    unknown_kind_error: str | None = None,
) -> BaseModel:
    """Match a flat discriminated input's fields to its member model, strictly.

    ``data`` is the flat input's supplied fields (omitted ones absent); the member
    model carries only its own fields and forbids the rest, so this is where a field
    that contradicts the discriminator becomes an error instead of a silent drop.
    ``noun`` names the thing in messages ("transformation", "derivation");
    ``unknown_kind_error`` is a format string with a ``{kind}`` placeholder,
    replacing the message for a discriminator value this union has no member for --
    the place to say what the caller should use instead.
    """
    raw = data.get(discriminator)
    kind = raw.value if hasattr(raw, "value") else raw
    data = {**data, discriminator: kind}

    member = members.get(kind)
    if member is None:
        raise ValueError((unknown_kind_error or "A {noun} cannot be a {kind}.").format(kind=kind, noun=noun))

    try:
        return member.model_validate(data)
    except ValidationError as err:
        raise ValueError(_member_mismatch(err, member=member, kind=kind, noun=noun, discriminator=discriminator)) from err


def camel_field(name: str) -> str:
    """A pydantic field name as the SDL spells it, for a message the client can act on."""
    head, *tail = name.split("_")
    return head + "".join(part.title() for part in tail)


def _an(kind: str) -> str:
    return f"An {kind}" if kind[:1] in "AEIOU" else f"A {kind}"


def _member_mismatch(err: ValidationError, *, member: type[BaseModel], kind: str, noun: str, discriminator: str) -> str:
    reads = [camel_field(name) for name in member.model_fields if name != discriminator]
    reads_clause = "it reads " + ", ".join(f"`{name}`" for name in reads) if reads else "it takes no parameters at all"
    for detail in err.errors():
        loc = [str(part) for part in detail["loc"] if str(part) != discriminator]
        field = camel_field(loc[0]) if loc else discriminator
        if detail["type"] == "extra_forbidden":
            return f"{_an(kind)} {noun} does not read `{field}`: {reads_clause}. Drop it, or pick the kind that reads it."
        if detail["type"] == "missing":
            return f"{_an(kind)} {noun} requires `{field}`"
    return describe_validation_error(err)


def describe_validation_error(err: ValidationError) -> str:
    """The first error of a pydantic ``ValidationError``, as one sentence of prose.

    A resolver's exception reaches the client as ``errors[0].message`` verbatim -- there is
    no error-masking extension and no error codes -- so in this codebase the message *is*
    the API contract, and every hand-written check states its rule in a full sentence. A
    raw ``ValidationError`` breaks that voice: it renders as a multi-line report carrying
    the model's class name, pydantic's ``[type=...]`` annotation and a docs URL. This keeps
    the sentence a validator wrote and drops the machinery around it.

    Only the first error is reported, for the same reason every ``assert_*`` in
    :mod:`core.logic` raises on the first thing it finds: a caller fixes one at a time, and
    the second is often a consequence of the first.
    """
    detail = err.errors()[0]
    message = str(detail["msg"])
    if message.startswith(_VALUE_ERROR_PREFIX):
        # A validator's own sentence, which already names its field where that matters.
        return message[len(_VALUE_ERROR_PREFIX) :]

    # Anything else is pydantic's own text for a constraint or a coercion. Name the field
    # by its full path, so an error on a nested input says which one -- `policy.nchildren`,
    # not `policy`. List indices stay bare, as they read better than `.0.`.
    parts = [str(part) if isinstance(part, int) else camel_field(str(part)) for part in detail["loc"]]
    if not parts:
        return message
    path = parts[0] + "".join(f"[{part}]" if part.isdigit() else f".{part}" for part in parts[1:])
    return f"`{path}`: {message}"


def prose_errors(cls: type[T]) -> type[T]:
    """Make a strawberry input's ``to_pydantic`` raise prose instead of a pydantic report.

    Applied *above* ``@kante.pydantic_input`` on the inputs whose models carry validators,
    so the sentence the validator wrote is the sentence the client reads. Only those inputs
    are wrapped: a model with no validators can only fail on a type coercion GraphQL has
    already done, so wrapping it would cost a try/except to catch nothing.

    The precedent is :class:`core.inputs.coords.TransformInput`, which hand-writes
    ``to_pydantic`` for the same reason -- to route its errors through
    :func:`parse_union_member`.
    """
    original: Callable = cls.to_pydantic  # type: ignore[attr-defined]

    def to_pydantic(self, **kwargs):  # noqa: ANN001, ANN003, ANN202 - a passthrough wrapper
        try:
            return original(self, **kwargs)
        except ValidationError as err:
            raise ValueError(describe_validation_error(err)) from err

    cls.to_pydantic = to_pydantic  # type: ignore[attr-defined]
    return cls
