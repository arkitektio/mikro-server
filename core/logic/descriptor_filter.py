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

from collections.abc import Sequence
from enum import Enum

import strawberry
from django.db.models import Q, QuerySet
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
