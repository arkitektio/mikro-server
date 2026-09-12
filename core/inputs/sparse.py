"""The axis input of a sparse dataset: a name, and what its positions are.

Split from :mod:`core.inputs.identification` when the identification union became shared with
the table path. What is left here is the part that is genuinely a sparse matrix's own: an axis
with no type (both axes of a sparse matrix enumerate, so INDEX is the only value the field
could ever hold) and nothing to declare but its name and what identifies it.
"""

from pydantic import BaseModel, ConfigDict

import strawberry

import kante

from core.inputs.identification import IdentificationInput, IdentificationSpec

_AXIS_NAME_DESCRIPTION = (
    "The axis' name, free-form and unique within this dataset -- `bin`, `gene`, `metabolite`, `neuron`. It is the name a colouring names a position along, and the name the "
    "server reports back in `indexableAxes`"
)


class SparseAxisInputModel(BaseModel):
    """One axis of a sparse dataset: its name, and what it is."""

    model_config = ConfigDict(extra="forbid")

    name: str
    identified_by: list[IdentificationSpec]
    long_name: str | None = None
    description: str | None = None


@kante.pydantic_input(
    SparseAxisInputModel,
    description=(
        "One axis of a sparse matrix, and what its positions **are**. `identifiedBy` is a list because fan-in is real -- a nucleus mask and a cell mask may key the same axis, "
        "one edge each -- and it may not be empty: an axis nothing identifies is not a lax dataset, it is one no source could ever key. There is no `type` field: both axes of a "
        "sparse matrix enumerate and neither has a metric, so INDEX is the only thing it could ever be"
    ),
)
class SparseAxisInput:
    """One axis of a sparse dataset."""

    name: str = strawberry.field(description=_AXIS_NAME_DESCRIPTION)
    identified_by: list[IdentificationInput] = strawberry.field(description="What this axis' positions are: sources whose contents are the ids, or the table whose rows they are. At least one; more than one is fan-in, which writes an edge per source")
    long_name: str | None = strawberry.field(default=None, description="A human-readable name for the axis")
    description: str | None = strawberry.field(default=None, description="What this axis enumerates, for a reader of the schema")

