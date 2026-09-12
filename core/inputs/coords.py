"""Input types for the coordinate system graph.

These live here rather than in ``core.mutations`` so the service layer in
``core.logic`` can reference them without importing a mutation module.

Two axis inputs, deliberately: a dataset's own axes are *structural* -- a name
and a semantic type, no unit, because the dataset's intrinsic space is its pixel
grid. Units only exist on unit-carrying spaces (a dataset's physical space, a
shared world), whose axes are supplied through :class:`PhysicalAxisInput`.
"""

import dataclasses
from typing import Annotated, ClassVar, Literal

import strawberry
from pydantic import BaseModel, ConfigDict, Field, field_validator

import kante
from kanne_server import scalars as kanne_scalars

from core import enums
from core.input_unions import parse_union_member, prose_errors, union_memberships
from core.logic import coords as coords_logic


# --------------------------------------------------------------------------------------
# The two value rules a metric transform obeys, as plain functions so the pydantic members
# above and `core.logic.graph` below can hold the same line -- exactly as the members'
# ``extra="forbid"`` and ``_assemble_edge_params`` already do for stray parameters.
#
# The first two reject a map that collapses an axis outright -- a zero factor, a zero row --
# which needs no determinant to be seen. The third asks the question they cannot: whether the
# map is invertible at all.
#
# What is still deliberately not asked is whether an *AFFINE's* matrix is rigid: RFC-8 draws
# that line ("proving it rigid needs an SVD, which is linear algebra inside a metadata
# answer"), and `invariance_of` keeps it -- an AFFINE reads AFFINE even when its matrix is a
# rotation. A matrix the caller has *labelled* ROTATION is a different question and is checked;
# see `assert_orthonormal`. A
# question the schema already claims to answer -- `is_invertible` reports invertibility by
# *kind*, so a singular matrix is offered to a client for backwards traversal with nothing
# to warn it -- and it is answered by the same elimination that would do the inverting, not
# by a determinant. See `coords.is_singular` for why those are not the same test.
#
# The shape decides whether that question applies, not the kind. An `affine` is M x (N+1) --
# one row per output axis, plus the translation column -- so its *linear part* is square
# exactly when `len(affine) == len(affine[0]) - 1`. A BY_DIMENSION's is (its named axes map
# one for one), and so is a same-rank AFFINE or a ROTATION; a rank-changing AFFINE's is not,
# and "invertible" is not a thing to ask of it.


def assert_no_collapsed_factors(scale: list[float], *, noun: str = "transformation") -> None:
    """Reject a scale factor of zero, which collapses its axis onto a point.

    Not a matter of taste: ``_scale_invariance`` classifies a scale by whether its entries
    are *equal*, so ``[0.0, 0.0]`` is reported as a SIMILARITY -- angles and length ratios
    preserved -- for a map that preserves nothing, and ``is_invertible`` is kind-only, so
    the client is then handed an ``inverted: true`` step it cannot honour. A negative factor
    is left alone: a mirrored axis is a real thing.
    """
    collapsed = [index for index, factor in enumerate(scale) if factor == 0.0]
    if collapsed:
        raise ValueError(
            f"A {noun}'s `scale` multiplies a coordinate, so no factor may be zero, but it is zero at {collapsed} ({scale}). A zero factor collapses that axis onto a point -- a projection is stated by naming the axes you keep (BY_DIMENSION), never by scaling one to nothing."
        )


def assert_no_collapsed_rows(affine: list[list[float]], *, noun: str = "transformation") -> None:
    """Reject an affine row whose linear part is all zeros, which collapses an output axis.

    The last column is the translation and is deliberately excluded -- a row that is all
    zeros *but* for its offset is a constant, which is the same collapse; a row whose only
    non-zero entry *is* in the linear part is fine at any magnitude.
    """
    collapsed = [index for index, row in enumerate(affine) if row and not any(entry != 0.0 for entry in row[:-1])]
    if collapsed:
        raise ValueError(
            f"A {noun}'s `affine` has one row per output axis, so no row's linear part may be all zeros, but rows {collapsed} are. Such a row sends every input to one value, collapsing that output axis -- a projection is stated by naming the axes you keep (BY_DIMENSION), never by zeroing a row."
        )


def assert_nonsingular_matrix(affine: list[list[float]], *, noun: str = "transformation") -> None:
    """Reject a square affine whose linear part cannot be undone.

    `core.logic.graph.is_invertible` decides invertibility by *kind*, and says so in its own
    docstring: "a **singular** square AFFINE (a projection written as a matrix, ``[1,1,0]``)
    is still offered for inversion, and only a determinant would catch it." This is that
    check, at the one moment the author is still in the room -- run as the elimination
    `invert_matrix` would run rather than as a determinant, for the reason
    :func:`~core.logic.coords.is_singular` gives. Left uncaught, the edge is handed to a
    placement walk, which offers it backwards, and the client gets an `inverted: true` step
    it cannot honour -- or the server does, composing a path.

    Only when the linear part is square: a rank-changing AFFINE is rectangular by design
    (`assert_edge_rank` admits it deliberately), and it has no inverse to ask about.
    """
    if not affine or not affine[0]:
        return
    linear = [row[:-1] for row in affine]
    if len(linear) != len(linear[0]):
        return
    if coords_logic.is_singular(linear):
        raise ValueError(
            f"A {noun}'s `affine` is square here, so it claims a map that can be undone -- but its linear part {linear} is singular: it collapses at least one axis onto the others, "
            "and no point of the output names one point of the input. A map that genuinely drops an axis is stated by naming the axes you keep (BY_DIMENSION), never by a singular matrix."
        )


class AxisInputModel(BaseModel):
    """One structural axis of a dataset's pixel grid, as supplied at ingest."""

    name: str
    type: enums.AxisType
    long_name: str | None = None
    description: str | None = None


@kante.pydantic_input(AxisInputModel, description="Input type for one structural axis of a dataset's pixel grid: its name and its semantic kind. Units and spacings do not belong here -- they belong to a physical space, a separate coordinate system plus one edge")
class AxisInput:
    """Input for one structural axis of a dataset's pixel grid."""

    name: str = strawberry.field(description="The name of the axis, e.g. 'z', 'c' or 'tau'. Free-form")
    type: enums.AxisType = strawberry.field(description="The semantic kind of the axis. Axes must be ordered by this: time first, then channel and custom types, then space")
    long_name: str | None = strawberry.field(default=None, description="A human-readable name for the axis")
    description: str | None = strawberry.field(default=None, description="A free-form description of what the axis measures, e.g. 'distance from the coverslip'")


class CoordinateInputModel(BaseModel):
    """One discrete coordinate pin: a coordinate name and the value along it."""

    name: str
    value: int


@kante.pydantic_input(CoordinateInputModel, description="A discrete coordinate an annotation is pinned to, e.g. a timepoint or a channel")
class CoordinateInput:
    """Input for pinning to a value along one named coordinate."""

    name: str = strawberry.field(description="The name of the coordinate, e.g. 't' or 'c'")
    value: int = strawberry.field(description="The value along that coordinate")


def at_map(at: "list[CoordinateInput] | None") -> dict[str, int] | None:
    """The `at` argument as the plain mapping the graph layer takes.

    Beside the input it reads rather than in either of the two type modules that take one:
    a layer's placement questions and a space's `inView` both accept `at`, and two copies of
    this three-line conversion are two chances for one of them to key the mapping differently
    from the selectors it is matched against.
    """
    return {pin.name: pin.value for pin in at} if at else None


class SelectorInputModel(BaseModel):
    """Where along one axis an edge applies: one axis name and one discrete index on it."""

    axis: str
    index: int


@kante.pydantic_input(
    SelectorInputModel,
    description="Where along one axis a transformation applies: the map holds at that index and makes no claim elsewhere",
)
class SelectorInput:
    """Input for scoping an edge to one position along one axis."""

    axis: str = strawberry.field(description="The axis of the *input* system this edge is scoped to, e.g. 'c' or 't'. It must be an axis you index rather than measure -- a SPACE axis is refused, because a correction that varies continuously through space is a FIELD")
    index: int = strawberry.field(description="The position along that axis at which this map holds, e.g. 2 for the third channel")


class BoundingBoxInputModel(BaseModel):
    """An axis-aligned box as a min and a max corner."""

    min: list[float]
    max: list[float]


@kante.pydantic_input(BoundingBoxInputModel, description="An axis-aligned box as a min and a max corner, in the coordinate order of the frame it is asked in")
class BoundingBoxInput:
    """Input for an axis-aligned bounding box."""

    min: list[float] = strawberry.field(description="The lower corner, in the frame's coordinate order")
    max: list[float] = strawberry.field(description="The upper corner, in the frame's coordinate order")


# --------------------------------------------------------------------------------------
# The transform input union.
#
# One edge of the coordinate graph arrives as the flat, discriminator-carrying
# ``TransformInput``: `kind` plus the union
# of every kind's parameter fields. The per-kind member models below are the strict
# truth about which fields each kind reads -- they forbid the rest, so a parameter that
# contradicts the kind is an error, never a silent drop -- and their input mirrors are
# published in the SDL under ``@unionElementOf`` so a generated client can rebuild the
# tagged union. Every kind gets a mirror, IDENTITY included: it takes no parameters, but
# the discriminator is itself a field, so the input is not empty and GraphQL is content.
# Leaving it out was not free -- a client that cannot build an IDENTITY member has no way
# to say "same grid" except by omitting the transform, and omitting the transform is how
# this schema says UNMAPPABLE. The two are opposites, so the gap did not degrade a
# generated client, it inverted it.


@dataclasses.dataclass(frozen=True)
class LoweredTransform:
    """A transform member flattened to the shape the graph writers take.

    ``kind`` is the value string; the rest are exactly the keyword arguments of
    :func:`core.logic.graph.build_registration_edge` and ``write_relation_edge``, so a
    resolver lowers once and passes through. ``field`` stays an unresolved ID: the
    resolver is the request-scoped place to fetch the system.
    """

    kind: str
    scale: list[float] | None = None
    translation: list[float] | None = None
    affine: list[list[float]] | None = None
    input_axes: list[str] | None = None
    output_axes: list[str] | None = None
    field: str | None = None
    reason: str | None = None


IDENTITY_TRANSFORM = LoweredTransform(kind=enums.TransformKind.IDENTITY.value)


#: How far a ROTATION's MᵀM may stray from the identity before it is not a rotation. Loose
#: enough for a matrix that came out of a registration solver in float32 and was rounded on
#: the way through JSON; far tighter than any real shear or scale.
_ORTHONORMAL_TOLERANCE = 1e-6


def assert_orthonormal(affine: list[list[float]], *, noun: str = "transformation") -> None:
    """Reject a ROTATION whose matrix is not one.

    A ROTATION carries the same `affine` an AFFINE does and differs from it in exactly one
    respect: `invariance_of` reads it ISOMETRY, so a client is told lengths and angles survive
    the map. Nothing checked that. The kind was a label the server took on trust -- which is
    the shape this codebase refuses everywhere else, "a schema that says yes where the server
    says no".

    **The reason it went unchecked does not apply to it.** The comment at the top of this
    module cited RFC-8 for "orthonormality is deliberately not checked", and RFC-8 says:
    *"AFFINE reads AFFINE even when its matrix happens to be a rotation. Proving it rigid needs
    an SVD."* That is the other question -- proving an *arbitrary* matrix rigid. Checking that a
    matrix the caller has *labelled* a rotation is orthonormal is ``MᵀM ≈ I``: one matmul and a
    comparison, on a 3x3. The justification was borrowed from a neighbouring argument it does
    not fit.

    Only the linear part, and only when it is square -- `assert_edge_rank` holds a ROTATION to
    square anyway, and a rectangular one is refused there with a better message than this could
    give.

    **A reflection passes, and that is correct.** ``MᵀM = I`` holds for the whole orthogonal
    group, not just the rotations, so a mirrored axis (determinant -1) is accepted. What the
    kind buys a client is `invariance_of` reporting ISOMETRY -- lengths and angles survive --
    and a reflection is an isometry. Refusing it would be checking a claim nobody makes.
    """
    if not affine or not affine[0]:
        return
    linear = [row[:-1] for row in affine]
    rank = len(linear)
    if any(len(row) != rank for row in linear):
        return

    worst_value, worst_cell = 0.0, None
    for i in range(rank):
        for j in range(rank):
            entry = sum(linear[k][i] * linear[k][j] for k in range(rank))
            deviation = abs(entry - (1.0 if i == j else 0.0))
            if deviation > worst_value:
                worst_value, worst_cell = deviation, (i, j)

    if worst_value > _ORTHONORMAL_TOLERANCE:
        kind = "a length" if worst_cell and worst_cell[0] == worst_cell[1] else "an angle"
        raise ValueError(
            f"A {noun} declared ROTATION must have an orthonormal matrix -- that is what makes it a rotation rather than a general map, and it is why a client is told lengths and "
            f"angles survive it. Its transpose-times-itself differs from the identity by {worst_value:.6g} at {worst_cell}, so it does not preserve {kind}. "
            "Use AFFINE for a matrix that scales or shears; ROTATION is the claim that it does neither."
        )

class IdentityTransformInputModel(BaseModel):
    """The identity map: no parameters beyond the discriminator that names it."""

    kind: Literal["IDENTITY"] = "IDENTITY"
    model_config = ConfigDict(extra="forbid")

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind)


class ScaleTransformInputModel(BaseModel):
    """A per-axis multiplication: `scale` has one entry per input axis."""

    kind: Literal["SCALE"] = "SCALE"
    scale: list[float]
    model_config = ConfigDict(extra="forbid")

    @field_validator("scale")
    @classmethod
    def _no_collapsed_factors(cls, scale: list[float]) -> list[float]:
        assert_no_collapsed_factors(scale)
        return scale

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, scale=self.scale)


class TranslationTransformInputModel(BaseModel):
    """A per-axis offset: `translation` has one entry per input axis."""

    kind: Literal["TRANSLATION"] = "TRANSLATION"
    translation: list[float]
    model_config = ConfigDict(extra="forbid")

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, translation=self.translation)


class AffineTransformInputModel(BaseModel):
    """A general affine map: `affine` is M x (N+1), rows outermost."""

    kind: Literal["AFFINE"] = "AFFINE"
    affine: list[list[float]]
    model_config = ConfigDict(extra="forbid")

    @field_validator("affine")
    @classmethod
    def _no_collapsed_rows(cls, affine: list[list[float]]) -> list[list[float]]:
        assert_no_collapsed_rows(affine)
        assert_nonsingular_matrix(affine)
        return affine

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, affine=self.affine)


class RotationTransformInputModel(BaseModel):
    """A rotation: `affine` is the orthonormal matrix, in an AFFINE's layout."""

    kind: Literal["ROTATION"] = "ROTATION"
    affine: list[list[float]]
    model_config = ConfigDict(extra="forbid")

    @field_validator("affine")
    @classmethod
    def _no_collapsed_rows(cls, affine: list[list[float]]) -> list[list[float]]:
        # A collapse, a singularity, and -- since 2026-08-21 -- orthonormality. A rotation is
        # square, `assert_edge_rank` holds it to that, so all three always apply. See
        # `assert_orthonormal` for why the RFC-8 line this used to cite is about a different
        # question than the one it was cited for.
        assert_no_collapsed_rows(affine)
        assert_nonsingular_matrix(affine)
        assert_orthonormal(affine)
        return affine

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, affine=self.affine)


class MapAxisTransformInputModel(BaseModel):
    """A pure permutation of axes; the two lists are the whole map."""

    kind: Literal["MAP_AXIS"] = "MAP_AXIS"
    input_axes: list[str]
    output_axes: list[str]
    model_config = ConfigDict(extra="forbid")

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, input_axes=self.input_axes, output_axes=self.output_axes)


class ByDimensionTransformInputModel(BaseModel):
    """A map over a named subset of axes, optionally with parameters over that subset."""

    kind: Literal["BY_DIMENSION"] = "BY_DIMENSION"
    input_axes: list[str]
    output_axes: list[str]
    scale: list[float] | None = None
    translation: list[float] | None = None
    affine: list[list[float]] | None = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("scale")
    @classmethod
    def _no_collapsed_factors(cls, scale: list[float] | None) -> list[float] | None:
        if scale is not None:
            assert_no_collapsed_factors(scale)
        return scale

    @field_validator("affine")
    @classmethod
    def _no_collapsed_rows(cls, affine: list[list[float]] | None) -> list[list[float]] | None:
        if affine is not None:
            assert_no_collapsed_rows(affine)
            # The case most worth catching: a BY_DIMENSION maps its named axes one for one,
            # so its linear part is always square, and a childless one is `is_invertible`
            # by kind -- meaning a singular one is offered for backwards traversal.
            assert_nonsingular_matrix(affine)
        return affine

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(
            kind=self.kind,
            input_axes=self.input_axes,
            output_axes=self.output_axes,
            scale=self.scale,
            translation=self.translation,
            affine=self.affine,
        )


class FieldTransformInputModel(BaseModel):
    """An array-valued map: `field` names the array's coordinate system."""

    kind: Literal["FIELD"] = "FIELD"
    field: str
    input_axes: list[str]
    output_axes: list[str]
    model_config = ConfigDict(extra="forbid")

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, field=self.field, input_axes=self.input_axes, output_axes=self.output_axes)


class UnmappableTransformInputModel(BaseModel):
    """A declared non-correspondence, with an optional reason."""

    kind: Literal["UNMAPPABLE"] = "UNMAPPABLE"
    reason: str | None = None
    model_config = ConfigDict(extra="forbid")

    def lower(self) -> LoweredTransform:
        """Flatten to the shape the graph writers take."""
        return LoweredTransform(kind=self.kind, reason=self.reason)


#: Every directly-creatable kind, keyed by discriminator value: the one union every
#: authored edge -- registration or derivation -- arrives through.
TRANSFORM_MEMBERS: dict[str, type[BaseModel]] = {
    "IDENTITY": IdentityTransformInputModel,
    "SCALE": ScaleTransformInputModel,
    "TRANSLATION": TranslationTransformInputModel,
    "AFFINE": AffineTransformInputModel,
    "ROTATION": RotationTransformInputModel,
    "MAP_AXIS": MapAxisTransformInputModel,
    "BY_DIMENSION": ByDimensionTransformInputModel,
    "FIELD": FieldTransformInputModel,
    "UNMAPPABLE": UnmappableTransformInputModel,
}

@kante.pydantic_input(
    IdentityTransformInputModel,
    directives=union_memberships("TransformInput", key="IDENTITY"),
    description="The fields an IDENTITY member of TransformInput reads -- only the discriminator, the map having no parameters. Published for codegen; the wire type is the flat TransformInput",
)
class IdentityTransformInput:
    """The IDENTITY member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")


@kante.pydantic_input(
    ScaleTransformInputModel,
    directives=union_memberships("TransformInput", key="SCALE"),
    description="The fields a SCALE member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class ScaleTransformInput:
    """The SCALE member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    scale: list[float] = strawberry.field(description="The per-axis scale factors, in the axis order of the input system")


@kante.pydantic_input(
    TranslationTransformInputModel,
    directives=union_memberships("TransformInput", key="TRANSLATION"),
    description="The fields a TRANSLATION member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class TranslationTransformInput:
    """The TRANSLATION member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    translation: list[float] = strawberry.field(description="The per-axis offsets, in the axis order of the input system")


@kante.pydantic_input(
    AffineTransformInputModel,
    directives=union_memberships("TransformInput", key="AFFINE"),
    description="The fields an AFFINE member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class AffineTransformInput:
    """The AFFINE member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    affine: list[list[float]] = strawberry.field(description="The matrix, M x (N+1), rows outermost. The last column is the translation")


@kante.pydantic_input(
    RotationTransformInputModel,
    directives=union_memberships("TransformInput", key="ROTATION"),
    description="The fields a ROTATION member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class RotationTransformInput:
    """The ROTATION member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    affine: list[list[float]] = strawberry.field(description="The orthonormal rotation matrix, in the same M x (N+1) layout an AFFINE uses")


@kante.pydantic_input(
    MapAxisTransformInputModel,
    directives=union_memberships("TransformInput", key="MAP_AXIS"),
    description="The fields a MAP_AXIS member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class MapAxisTransformInput:
    """The MAP_AXIS member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    input_axes: list[str] = strawberry.field(description="The names of the input axes, e.g. ['z', 'y', 'x']")
    output_axes: list[str] = strawberry.field(description="The names of the output axes they map onto, position by position. The matrix is synthesized from the two lists")


@kante.pydantic_input(
    ByDimensionTransformInputModel,
    directives=union_memberships("TransformInput", key="BY_DIMENSION"),
    description="The fields a BY_DIMENSION member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class ByDimensionTransformInput:
    """The BY_DIMENSION member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    input_axes: list[str] = strawberry.field(description="The names of the input axes this edge acts on, e.g. ['y', 'x'] for a (c,y,x) dataset placed into a (t,z,y,x) world. The axes it does not name it says nothing about")
    output_axes: list[str] = strawberry.field(description="The names of the output axes they map onto")
    scale: list[float] | None = strawberry.field(default=None, description="Optional per-axis scale factors over the named axes, in the order they are named")
    translation: list[float] | None = strawberry.field(default=None, description="Optional per-axis offsets over the named axes")
    affine: list[list[float]] | None = strawberry.field(default=None, description="Optional matrix over the named axes, M x (N+1), rows outermost")


@kante.pydantic_input(
    FieldTransformInputModel,
    directives=union_memberships("TransformInput", key="FIELD"),
    description="The fields a FIELD member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class FieldTransformInput:
    """The FIELD member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    field: strawberry.ID = strawberry.field(
        description="The coordinate system of the array whose values are the map. Its value axis says what they mean -- COORDINATE for absolute positions, DISPLACEMENT for offsets, none at all for a scalar array whose one value is a position. Pass the input's own system when the array's pixels are themselves the map, as for a label mask keying a table of objects. A FIELD has no closed-form inverse, so a placement path only ever walks it forwards"
    )
    input_axes: list[str] = strawberry.field(description="The input axes the lookup consumes, e.g. ['y', 'x'] for a label mask -- the ones it does not name pass through")
    output_axes: list[str] = strawberry.field(description="The output axes the field's values produce, e.g. ['i']")


@kante.pydantic_input(
    UnmappableTransformInputModel,
    directives=union_memberships("TransformInput", key="UNMAPPABLE"),
    description="The fields an UNMAPPABLE member of TransformInput reads. Published for codegen; the wire type is the flat TransformInput",
)
class UnmappableTransformInput:
    """The UNMAPPABLE member of the transform input union."""

    kind: enums.CreatableTransformKind = strawberry.field(description="The discriminator: which member of TransformInput this is")
    reason: str | None = strawberry.field(default=None, description="Why nothing corresponds, e.g. 'one row per segmented object'. Purely descriptive: the kind is what the graph acts on")


#: The member inputs published to the SDL, for the schema's ``types=[...]``. Nothing here
#: is referenced by a field, so dropping one erases it from the SDL silently.
transform_union_types: list[type] = [
    IdentityTransformInput,
    ScaleTransformInput,
    TranslationTransformInput,
    AffineTransformInput,
    RotationTransformInput,
    MapAxisTransformInput,
    ByDimensionTransformInput,
    FieldTransformInput,
    UnmappableTransformInput,
]


#: The union the pydantic side carries: a `transform` field holds one *member* model, so
#: a resolver never sees the flat wire shape at all. The wire lie -- GraphQL has no input
#: unions, so the SDL type is flat -- is corrected exactly once, in the strawberry
#: inputs' hand-written ``to_pydantic`` below.
TransformSpec = Annotated[
    IdentityTransformInputModel
    | ScaleTransformInputModel
    | TranslationTransformInputModel
    | AffineTransformInputModel
    | RotationTransformInputModel
    | MapAxisTransformInputModel
    | ByDimensionTransformInputModel
    | FieldTransformInputModel
    | UnmappableTransformInputModel,
    Field(discriminator="kind"),
]

@strawberry.input(
    description="One edge of the coordinate graph, as a discriminated union: `kind` selects a member, and only that member's fields are read -- any other supplied field is rejected, never dropped. The member inputs annotated `@unionElementOf(union: \"TransformInput\")` say which fields each kind reads. Direction is always forward, input -> output",
)
class TransformInput:
    """One authored edge of the coordinate graph, discriminated by `kind`.

    Deliberately not pydantic-backed: the wire type is flat because GraphQL has no
    input unions, and ``to_pydantic`` is where that flatness is corrected into the
    strict member model -- so the pydantic layer only ever holds the union.
    """

    kind: enums.CreatableTransformKind = strawberry.field(description="The kind of transformation, which fixes which of the fields below are read. Any field outside the chosen kind's member is rejected")
    scale: list[float] | None = strawberry.field(default=None, description="(SCALE, BY_DIMENSION) The per-axis scale factors")
    translation: list[float] | None = strawberry.field(default=None, description="(TRANSLATION, BY_DIMENSION) The per-axis offsets")
    affine: list[list[float]] | None = strawberry.field(default=None, description="(AFFINE, ROTATION, BY_DIMENSION) The matrix, M x (N+1), rows outermost")
    input_axes: list[str] | None = strawberry.field(default=None, description="(MAP_AXIS, BY_DIMENSION, FIELD) The names of the input axes the edge acts on")
    output_axes: list[str] | None = strawberry.field(default=None, description="(MAP_AXIS, BY_DIMENSION, FIELD) The names of the output axes they map onto")
    field: strawberry.ID | None = strawberry.field(default=None, description="(FIELD) The coordinate system of the array whose values are the map")
    reason: str | None = strawberry.field(default=None, description="(UNMAPPABLE) Why nothing corresponds. Purely descriptive")

    def to_pydantic(self) -> BaseModel:
        """Match the flat wire fields to the member model `kind` selects, strictly."""
        supplied = {
            "kind": self.kind,
            "scale": self.scale,
            "translation": self.translation,
            "affine": self.affine,
            "input_axes": self.input_axes,
            "output_axes": self.output_axes,
            "field": self.field,
            "reason": self.reason,
        }
        data = {name: value for name, value in supplied.items() if value is not None}
        return parse_union_member(TRANSFORM_MEMBERS, data, noun="transformation")


# --------------------------------------------------------------------------------------
# The derivation source union.
#
# "This data was computed from that data" is one edge, whatever is at either end -- an
# image from an image, a measurement table from an instance mask, an image reconstructed
# from a table of localizations. What used to differ was only who could be *named*: an
# array dataset named a `lens` and nothing else, while the three collections named a bare
# `coordinateSystem`, so the caller had to look the source's system up by hand and no
# collection could ever be a source.
#
# One union now, keyed by source kind, the third instance of the `@unionElementOf`
# convention. Direction is always child -> source: the new data's own space is the input,
# the source's space is the output, exactly as `write_relation_edge` writes it.


@dataclasses.dataclass(frozen=True)
class LoweredDerivation:
    """A derivation member flattened to what a resolver needs: which source, and which edge.

    ``source_id`` stays an unresolved id, the same rule :class:`LoweredTransform` keeps for
    ``field``: this module knows about enums and pydantic and must not learn about
    ``core.models``, so fetching and org-scoping stay with the request-scoped resolver.
    """

    source_kind: str
    source_id: str
    transform: LoweredTransform
    value_relation: "enums.ValueRelation | None" = None


#: What an omitted `transform` means. **UNMAPPABLE, not IDENTITY** -- naming a source is not
#: the same as claiming a map, and a fabricated identity both lies when the units differ and
#: outranks a real edge in the placement walk. Stating the geometry is one field away; having
#: it invented for you is not recoverable, because nothing downstream can tell an assumed
#: identity from a measured one.
UNMAPPABLE_TRANSFORM = LoweredTransform(kind=enums.TransformKind.UNMAPPABLE.value)


class DerivedFromInputBase(BaseModel):
    """The fields every derivation carries, whichever kind of source it names."""

    model_config = ConfigDict(extra="forbid")

    kind: enums.DerivationSourceKind
    transform: TransformSpec | None = None
    value_relation: enums.ValueRelation | None = None

    #: The member's own id field, so `lower` needs no per-member override. A ClassVar, so
    #: pydantic treats it as neither a field nor a private attribute.
    SOURCE_FIELD: ClassVar[str] = "source"

    def lower(self) -> LoweredDerivation:
        """Flatten to what a resolver needs."""
        return LoweredDerivation(
            source_kind=self.kind.value if hasattr(self.kind, "value") else self.kind,
            source_id=getattr(self, type(self).SOURCE_FIELD),
            transform=self.transform.lower() if self.transform else UNMAPPABLE_TRANSFORM,
            value_relation=self.value_relation,
        )


class LensDerivedFromInputModel(DerivedFromInputBase):
    """Derived from a selection over an array dataset."""

    kind: Literal[enums.DerivationSourceKind.LENS] = enums.DerivationSourceKind.LENS
    lens: str
    SOURCE_FIELD: ClassVar[str] = "lens"


class DatasetDerivedFromInputModel(DerivedFromInputBase):
    """Derived from an array dataset as a whole, through its pixel grid."""

    kind: Literal[enums.DerivationSourceKind.DATASET] = enums.DerivationSourceKind.DATASET
    dataset: str
    SOURCE_FIELD: ClassVar[str] = "dataset"


class TableDatasetDerivedFromInputModel(DerivedFromInputBase):
    """Derived from a table dataset, through the space its coordinate columns declare."""

    kind: Literal[enums.DerivationSourceKind.TABLE_DATASET] = enums.DerivationSourceKind.TABLE_DATASET
    table_dataset: str
    SOURCE_FIELD: ClassVar[str] = "table_dataset"


class MeshCollectionDerivedFromInputModel(DerivedFromInputBase):
    """Derived from a mesh collection, through its vertex coordinate system."""

    kind: Literal[enums.DerivationSourceKind.MESH_COLLECTION] = enums.DerivationSourceKind.MESH_COLLECTION
    mesh_collection: str
    SOURCE_FIELD: ClassVar[str] = "mesh_collection"


class AnnotationCollectionDerivedFromInputModel(DerivedFromInputBase):
    """Derived from an annotation collection, through the space its shapes are drawn in."""

    kind: Literal[enums.DerivationSourceKind.ANNOTATION_COLLECTION] = enums.DerivationSourceKind.ANNOTATION_COLLECTION
    annotation_collection: str
    SOURCE_FIELD: ClassVar[str] = "annotation_collection"


class CoordinateSystemDerivedFromInputModel(DerivedFromInputBase):
    """Derived from a coordinate system directly -- a physical space, a world."""

    kind: Literal[enums.DerivationSourceKind.COORDINATE_SYSTEM] = enums.DerivationSourceKind.COORDINATE_SYSTEM
    coordinate_system: str
    SOURCE_FIELD: ClassVar[str] = "coordinate_system"


#: Every source kind, keyed by discriminator value.
DERIVED_FROM_MEMBERS: dict[str, type[BaseModel]] = {
    enums.DerivationSourceKind.LENS.value: LensDerivedFromInputModel,
    enums.DerivationSourceKind.DATASET.value: DatasetDerivedFromInputModel,
    enums.DerivationSourceKind.TABLE_DATASET.value: TableDatasetDerivedFromInputModel,
    enums.DerivationSourceKind.MESH_COLLECTION.value: MeshCollectionDerivedFromInputModel,
    enums.DerivationSourceKind.ANNOTATION_COLLECTION.value: AnnotationCollectionDerivedFromInputModel,
    enums.DerivationSourceKind.COORDINATE_SYSTEM.value: CoordinateSystemDerivedFromInputModel,
}

#: The union the pydantic side carries, so a resolver never sees the flat wire shape.
DerivedFromSpec = Annotated[
    LensDerivedFromInputModel
    | DatasetDerivedFromInputModel
    | TableDatasetDerivedFromInputModel
    | MeshCollectionDerivedFromInputModel
    | AnnotationCollectionDerivedFromInputModel
    | CoordinateSystemDerivedFromInputModel,
    Field(discriminator="kind"),
]

#: The wire fields carrying a source id, one per member.
_DERIVED_FROM_SOURCE_FIELDS = ("lens", "dataset", "table_dataset", "mesh_collection", "annotation_collection", "coordinate_system")

_TRANSFORM_DESCRIPTION = (
    "How this data's own space maps back into the source's -- any creatable kind; the rank check holds you to it. **Omit it and the edge is UNMAPPABLE**: naming a source records "
    "the lineage and claims no geometry, which is the truth for a table of per-object measurements whose rows are not anywhere. State IDENTITY for an in-place operation, TRANSLATION "
    "for a crop, SCALE for a resample or for a localization table's nanometres into a reconstruction's pixels, BY_DIMENSION for a projection that drops an axis. Only a mappable "
    "edge carries placement: derived data sits where its source sits exactly when it says how"
)

_VALUE_RELATION_DESCRIPTION = (
    "What the derivation did to the *values* -- orthogonal to the transform's `kind`, which only says where the data sits: IDENTICAL for a crop or reorder (statistics transfer), "
    "TRANSFORMED for a deconvolution, a normalization, or a table of measurements read off an image, CATEGORIZED for a threshold or segmentation (values became labels -- a "
    "bootstrapped scene then renders as a label map). Omit when unstated; the algorithm itself belongs to task provenance"
)


@prose_errors
@strawberry.input(
    description=(
        "Where this data came from, as a discriminated union: `kind` selects which sort of source is being named, and only that member's id field is read -- any other is rejected. "
        'The member inputs annotated `@unionElementOf(union: "DerivedFromInput")` say which field each kind reads. Direction is always this data -> its source'
    ),
)
class DerivedFromInput:
    """One source this data was computed from, discriminated by `kind`.

    Deliberately not pydantic-backed: the wire type is flat because GraphQL has no input
    unions, and ``to_pydantic`` is where that flatness is corrected into the strict member.
    """

    kind: enums.DerivationSourceKind = strawberry.field(description="Which sort of thing the source is. It fixes which id field below is read; any other is rejected")
    lens: strawberry.ID | None = strawberry.field(default=None, description="(LENS) The lens this data was computed from")
    dataset: strawberry.ID | None = strawberry.field(default=None, description="(DATASET) The array dataset this data was computed from, through its whole pixel grid")
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="(TABLE_DATASET) The table this data was computed from -- an SMLM localization table a reconstruction was rendered from, say")
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="(MESH_COLLECTION) The mesh collection this data was computed from")
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="(ANNOTATION_COLLECTION) The annotation collection this data was computed from")
    coordinate_system: strawberry.ID | None = strawberry.field(default=None, description="(COORDINATE_SYSTEM) The space this data was computed from, when the source is a space rather than a container")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)

    def to_pydantic(self) -> BaseModel:
        """Match the flat wire fields to the member model `kind` selects, strictly."""
        supplied = {name: getattr(self, name) for name in ("kind", "value_relation", *_DERIVED_FROM_SOURCE_FIELDS)}
        data = {name: value for name, value in supplied.items() if value is not None}
        if self.transform is not None:
            # The nested union is corrected first, so a bad transform is reported as a
            # transform error rather than as a shapeless one about the derivation.
            data["transform"] = self.transform.to_pydantic()
        return parse_union_member(DERIVED_FROM_MEMBERS, data, noun="derivation")


def _derived_from_member(model: type, key: "enums.DerivationSourceKind", description: str):  # noqa: ANN202 - a decorator factory
    """Publish one member input of the DerivedFromInput union."""
    return kante.pydantic_input(
        model,
        directives=union_memberships("DerivedFromInput", key=key.value),
        description=f"{description}. Published for codegen; the wire type is the flat DerivedFromInput",
    )


@_derived_from_member(LensDerivedFromInputModel, enums.DerivationSourceKind.LENS, "The fields a LENS derivation reads")
class LensDerivedFromInput:
    """The LENS member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    lens: strawberry.ID = strawberry.field(description="The lens this data was computed from")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_derived_from_member(DatasetDerivedFromInputModel, enums.DerivationSourceKind.DATASET, "The fields a DATASET derivation reads")
class DatasetDerivedFromInput:
    """The DATASET member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    dataset: strawberry.ID = strawberry.field(description="The array dataset this data was computed from, through its whole pixel grid")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_derived_from_member(TableDatasetDerivedFromInputModel, enums.DerivationSourceKind.TABLE_DATASET, "The fields a TABLE_DATASET derivation reads")
class TableDatasetDerivedFromInput:
    """The TABLE_DATASET member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    table_dataset: strawberry.ID = strawberry.field(description="The table this data was computed from")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_derived_from_member(MeshCollectionDerivedFromInputModel, enums.DerivationSourceKind.MESH_COLLECTION, "The fields a MESH_COLLECTION derivation reads")
class MeshCollectionDerivedFromInput:
    """The MESH_COLLECTION member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    mesh_collection: strawberry.ID = strawberry.field(description="The mesh collection this data was computed from")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_derived_from_member(AnnotationCollectionDerivedFromInputModel, enums.DerivationSourceKind.ANNOTATION_COLLECTION, "The fields an ANNOTATION_COLLECTION derivation reads")
class AnnotationCollectionDerivedFromInput:
    """The ANNOTATION_COLLECTION member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    annotation_collection: strawberry.ID = strawberry.field(description="The annotation collection this data was computed from")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


@_derived_from_member(CoordinateSystemDerivedFromInputModel, enums.DerivationSourceKind.COORDINATE_SYSTEM, "The fields a COORDINATE_SYSTEM derivation reads")
class CoordinateSystemDerivedFromInput:
    """The COORDINATE_SYSTEM member of the derivation source union."""

    kind: enums.DerivationSourceKind = strawberry.field(description="The discriminator: which member of DerivedFromInput this is")
    coordinate_system: strawberry.ID = strawberry.field(description="The space this data was computed from")
    transform: TransformInput | None = strawberry.field(default=None, description=_TRANSFORM_DESCRIPTION)
    value_relation: enums.ValueRelation | None = strawberry.field(default=None, description=_VALUE_RELATION_DESCRIPTION)


#: The member inputs published to the SDL, for the schema's ``types=[...]``. Dropping one
#: erases it from the SDL silently -- they are referenced by no field.
derived_from_union_types: list[type] = [
    LensDerivedFromInput,
    DatasetDerivedFromInput,
    TableDatasetDerivedFromInput,
    MeshCollectionDerivedFromInput,
    AnnotationCollectionDerivedFromInput,
    CoordinateSystemDerivedFromInput,
]


class PhysicalAxisInputModel(BaseModel):
    """One axis of a unit-carrying coordinate system (a physical space, a shared world)."""

    name: str
    type: enums.AxisType
    unit: str
    long_name: str | None = None
    description: str | None = None


@kante.pydantic_input(PhysicalAxisInputModel, description="Input type for one axis of a unit-carrying coordinate system: its name, its semantic kind and its physical unit")
class PhysicalAxisInput:
    """Input for one axis of a unit-carrying coordinate system."""

    name: str = strawberry.field(description="The name of the axis, e.g. 'z' or 't'. Free-form")
    type: enums.AxisType = strawberry.field(description="The semantic kind of the axis. Must match the pixel axis at the same position when the space reinterprets a dataset's grid")
    unit: kanne_scalars.Unit = strawberry.field(description="The physical unit of the axis, e.g. 'micrometer' or 'millisecond'. A pint unit, validated on the way in; 'a.u.' for arbitrary units")
    long_name: str | None = strawberry.field(default=None, description="A human-readable name for the axis")
    description: str | None = strawberry.field(default=None, description="A free-form description of what the axis measures, e.g. 'distance from the coverslip'")


class RegistrationPathInputModel(BaseModel):
    """A source to register into a shared coordinate system, plus the edge that places it.

    Exactly one source (a dataset, a table dataset, a mesh collection, or a bare coordinate
    system) is resolved to its own coordinate system; ``transform`` is the same edge,
    and the same rank check, that ``createTransformation`` writes -- direction is always
    source -> space.
    """

    dataset: str | None = None
    table_dataset: str | None = None
    mesh_collection: str | None = None
    annotation_collection: str | None = None
    coordinate_system: str | None = None
    transform: TransformSpec | None = None
    name: str | None = None
    validity: enums.PlacementValidity | None = None


@kante.pydantic_input(
    RegistrationPathInputModel,
    description="A source (dataset, table dataset, mesh collection, or coordinate system) to register into a shared space, plus the edge that places it. The edge points from the source's own coordinate system to the shared space; the transform is validated exactly as createTransformation validates one",
)
class RegistrationPathInput:
    """One source registered into a shared coordinate system, and the edge placing it."""

    dataset: strawberry.ID | None = strawberry.field(default=None, description="Register this dataset, through its intrinsic (pixel) coordinate system. Provide exactly one source")
    table_dataset: strawberry.ID | None = strawberry.field(default=None, description="Register this table dataset, through its own coordinate system (its declared coordinate columns). Provide exactly one source")
    mesh_collection: strawberry.ID | None = strawberry.field(default=None, description="Register this mesh collection, through its own vertex coordinate system. Provide exactly one source")
    annotation_collection: strawberry.ID | None = strawberry.field(default=None, description="Register this annotation collection, through its own drawing coordinate system. Provide exactly one source")
    coordinate_system: strawberry.ID | None = strawberry.field(default=None, description="Register this coordinate system directly. Provide exactly one source")
    transform: TransformInput | None = strawberry.field(
        default=None,
        description="The edge from the source into the shared space. Omit for an IDENTITY -- the source's coordinates are the space's coordinates as-is. Direction is always forward -- if your registration library gave you the inverse, invert it first",
    )
    name: str | None = strawberry.field(default=None, description="Optional name for the registration edge")
    validity: enums.PlacementValidity | None = strawberry.field(default=None, description="How much this map is actually known. Defaults to MANUAL -- someone authored it")


class ScenePolicyInputModel(BaseModel):
    """The policy a scene-from-coordinate-system build follows: which sources, how many, drawn how."""

    nchildren: int = 8
    transform_tables: bool = False
    include_meshes: bool = True
    include_networks: bool = True
    skip_unplaceable: bool = False
    kind: enums.BootstrapLayerKind | None = None

    @field_validator("nchildren")
    @classmethod
    def _caps_at_least_one_layer(cls, nchildren: int) -> int:
        """Reject a cap that materializes nothing.

        The build breaks out of its loop the moment it has materialized ``nchildren``
        sources, so zero returns a successfully-created scene with no layers at all -- from the
        client's side indistinguishable from a space with nothing in it, which is the one
        answer the caller cannot act on. A caller who wants no layers wants `createScene`.
        """
        if nchildren < 1:
            raise ValueError(f"`nchildren` caps how many sources the scene is built from, so it must be at least 1, but got {nchildren}. A scene with no layers is `createScene`.")
        return nchildren


@prose_errors
@kante.pydantic_input(
    ScenePolicyInputModel,
    description="The policy createSceneFromCoordinateSystem follows: at most `nchildren` sources, materialized from what lives in or is registered into the space, filtered by source kind and drawn by the recipe in `kind`. A source may become several layers -- a multi-channel image becomes one layer per channel",
)
class ScenePolicyInput:
    """How a scene is materialized from what a space holds."""

    nchildren: int = strawberry.field(default=8, description="The maximum number of *sources* to materialize, in registration (pk) order, and at least 1. Sources, not layers: a multi-channel image becomes one layer per channel, and a cap counted in layers would truncate mid-acquisition. A flat cap on the scene's size, not a tree of sub-scenes -- for a scene with no layers at all, use `createScene`")
    transform_tables: bool = strawberry.field(default=False, description="Whether to turn registered table datasets into point/track layers. Off by default: a table is often a per-object measurement with no place in a scene")
    include_meshes: bool = strawberry.field(default=True, description="Whether to turn registered mesh collections into mesh layers")
    include_networks: bool = strawberry.field(default=True, description="Whether to turn registered network collections -- traced arbors, vessel trees, connectomes -- into network layers")
    skip_unplaceable: bool = strawberry.field(
        default=False,
        description=(
            "What to do with a source the scene's world does not place affinely -- registered only across a FIELD, or not registered at all. Off by default, so the build "
            "fails naming the edge, which is the same rule `createLayer` applies and is easier to notice than a layer that quietly is not there. On, such a source is "
            "skipped exactly as an array too small to render or a table with too few coordinate columns already is -- and, like those, a skipped source does not count "
            "against `nchildren`, which caps the sources materialized rather than the sources looked at"
        ),
    )
    kind: enums.BootstrapLayerKind | None = strawberry.field(
        default=None,
        description=(
            "The render recipe for the **image** layers, overriding what would be inferred from the data's axes and from what ingest recorded. Says nothing about mesh, point, track or annotation "
            "layers, which have no recipe to choose. Worth passing for LABEL: it is the one recipe never inferred from structure -- nothing about an array distinguishes a label map from an image -- "
            "so an imported mask whose derivation was never declared CATEGORIZED renders as intensity unless you say otherwise. RGB is inferred, but only from recorded evidence (channels labelled "
            "red, green and blue, or a PNG/JPEG source file), so pass it for a photograph that arrived with neither. Omit to infer per source"
        ),
    )
