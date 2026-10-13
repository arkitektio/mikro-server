"""Every spatial derivation in the coordinate graph, in one place.

Nothing in this module may be inlined at a call site. That is not a style
preference: a transposed coordinate or a dropped half-voxel offset does not
raise, it just puts things in the wrong place, plausibly, and it will be found
years later by someone measuring the wrong cell. The permutation between array
order and vertex order in particular is one named, tested function precisely
because every copy of it is a place where the transpose can silently invert.

Several comments here and in `core.logic.graph` cite "item N of the proposals doc". That
document is **`testing/MIKRO_BACKEND_PROPOSALS.md`, in the `testing` repository** -- not this
one, which is why grepping for it here finds nothing. It is the running record of known gaps
in this layer and their shipped state; item 14 is the x/y/z transposition the render-axis
derivation below can still be given.

The conventions this module encodes:

* **The voxel centre is the origin.** Voxel ``n`` occupies ``[n - 0.5, n + 0.5)``,
  so an ROI on voxel 340 has an edge at 340.5. This is the RFC-5 half-open
  convention, and it is why a downsample introduces a half-voxel offset at all.
* **Array order is slowest-varying first** (``..., z, y, x``), which is the order
  of a numpy shape tuple. Vertex/GPU order is ``(x, y, z)``. They are reverses of
  each other over the spatial axes, and confusing them is the failure mode of the
  whole architecture.
* **Pyramid scales are absolute, never relative.** A level's scale is derived from
  the actual shapes, not from a nominal ``2 ** level``, and the derived value is
  what gets stored. It is a dimensionless pixel-to-pixel ratio: physical space
  enters the model exactly once, as a physical-space edge off the intrinsic system,
  never through the pyramid.
"""

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence

from kanne_server import scalars as kanne_scalars

from core import enums

# There is no axis *type* ordering rule and no rank table any more. RFC-5 states one --
# time, then channel and custom types, then space -- and it was validated at ingest for
# every array-backed system, but nothing in this module ever read it. `resolve_render_axes`
# finds the time, channel and phasor axes by a type scan, and the spatial axes through
# `spatial_axes()`, so where a TIME or CHANNEL axis sits among them changes nothing that is
# computed here. `create_table_axes` had already reasoned this out for tables and skipped
# the check; what the check actually did for arrays was refuse ordinary stores -- (z, c, y, x)
# and (c, z, y, x) are how real acquisitions are written -- to protect a derivation that does
# not consult it.
#
# What *is* load-bearing is the relative order of the SPACE axes (the last is x, the one
# before it y), and that is enforced by nothing here either: it is the convention
# `resolve_render_axes` falls back to when the axes are not named for the screen. An axis'
# `order` remains the store's dimension order, which is the rule that actually describes
# bytes, and it is written by enumeration rather than accepted from a caller.

# The axis types a pyramid may downsample: the *continuous* ones. Striding a
# long timelapse, re-binning FLIM arrival times or re-binning a spectrum is as
# meaningful as spatial downsampling and uses the same half-voxel arithmetic.
# Categorical axes stay out: c=0.5 between two channels means nothing.
_DOWNSAMPLABLE_TYPES: frozenset[str] = frozenset(
    {
        enums.AxisTypeChoices.SPACE.value,
        enums.AxisTypeChoices.TIME.value,
        enums.AxisTypeChoices.MICROTIME.value,
        enums.AxisTypeChoices.SPECTRUM.value,
    }
)

# The axis types a phasor may be taken over: the continuous, periodic-ish ones a
# discrete Fourier transform means something over. A DFT along z or c is not a
# slightly-odd rendering choice, it is arithmetic over an axis whose coordinates
# are positions or acquisition indices rather than samples of a periodic signal.
_PHASOR_TYPES: frozenset[str] = frozenset(
    {
        enums.AxisTypeChoices.MICROTIME.value,
        enums.AxisTypeChoices.SPECTRUM.value,
    }
)


# The physical dimension an axis type's unit must have. A TIME axis measured in
# micrometres is not a slightly-off calibration, it is a lie the arithmetic will
# happily propagate: seconds and metres compose into the same matrix. Types absent
# from this map (CHANNEL, COORDINATE, DISPLACEMENT) index into something with no
# agreed dimension, so any parseable unit is allowed.
_UNIT_DIMENSION_BY_TYPE: dict[str, str] = {
    enums.AxisTypeChoices.TIME.value: "[time]",
    enums.AxisTypeChoices.MICROTIME.value: "[time]",
    enums.AxisTypeChoices.SPECTRUM.value: "[length]",
    enums.AxisTypeChoices.SPACE.value: "[length]",
}


class AxisOrderError(ValueError):
    """Raised when a coordinate system's axes cannot describe a space: two share a name."""


class AxisUnitError(ValueError):
    """Raised when a calibrated axis' unit does not have the dimension its type requires."""


class NonAffineTransformError(ValueError):
    """Raised when a transformation that must be affine is not (e.g. a displacement field)."""


class SingularTransformError(NonAffineTransformError):
    """Raised when a square map has no inverse: its matrix is singular.

    A subclass, because every caller that already handles "this path has no closed form"
    handles this correctly too -- a map that cannot be undone is one more reason a path does
    not condense. It is named separately because it is the one such reason that is a
    property of the *numbers* rather than of the kind, and so the only one a write-time
    check can catch before anybody asks (see `core.inputs.coords.assert_nonsingular_matrix`).
    """


@dataclass(frozen=True)
class AxisSpec:
    """The subset of an axis this module needs, so the logic never touches the ORM.

    ``Axis`` rows, ingest inputs and test fixtures all coerce into this. Only the
    name and the semantic type are load-bearing: units live on calibrated systems'
    axes and never enter a derivation *from one system's axes alone*, so they are not
    carried here. What does enter a derivation is a unit **pair** -- what one side calls
    an axis against what the other side calls it -- and a pair belongs to the edge, not
    to either endpoint. `AxedStep` carries it; see `_pass_through_factor`.
    """

    name: str
    type: str


@dataclass(frozen=True)
class RenderAxes:
    """The array-axis names a renderer maps to screen x, y, z, time and intensity."""

    x: str
    y: str
    z: str | None
    t: str | None
    intensity: str | None
    phasor: str | None
    vector: str | None


class SliceLike(Protocol):
    """What this module reads off a slice: a pydantic ``SliceModel``, a GraphQL input, a test stub."""

    axis: str
    start: int | None
    stop: int | None
    step: int | None


def assert_axis_names_unique(axes: Sequence[AxisSpec]) -> None:
    """Enforce that no two axes of one coordinate system share a name.

    The database already refuses this -- ``Axis.Meta.unique_together`` carries
    ``("coordinate_system", "name")`` -- but every axis writer uses ``bulk_create``, so
    what the caller gets back is a raw ``IntegrityError`` naming a Postgres constraint.
    A name is how every edge refers to an axis (``inputAxes``, ``outputAxes``, an
    annotation's coordinate pins), so a duplicate is worth a sentence rather than a 500.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for axis in axes:
        if axis.name in seen:
            duplicates.add(axis.name)
        seen.add(axis.name)

    if duplicates:
        given = ", ".join(axis.name for axis in axes)
        raise AxisOrderError(f"The axes of a coordinate system are named uniquely -- an edge names an axis to act on it -- but {sorted(duplicates)} appears more than once in [{given}]")


def assert_unit_matches_type(axis_name: str, axis_type: str, unit: str) -> None:
    """Enforce that a calibrated axis' unit has the dimension its type requires.

    A SPACE axis in seconds and a TIME axis in micrometres are both accepted today,
    and neither fails anywhere: the unit is never read by an arithmetic path, so the
    error surfaces years later as a plot with the wrong axis. Rejected at write time
    instead, where the author is still in the room.

    ``a.u.`` stays the escape hatch, for an axis whose unit genuinely is arbitrary.
    """
    required = _UNIT_DIMENSION_BY_TYPE.get(axis_type)
    if required is None or kanne_scalars.is_arbitrary_unit(unit):
        return

    dimensionality = kanne_scalars.get_registry().Unit(kanne_scalars.normalize_compact_units(unit)).dimensionality
    if dict(dimensionality) != {required: 1}:
        raise AxisUnitError(f"Axis '{axis_name}' is a {axis_type} axis, so its unit must measure {required}, but '{unit}' does not. Use 'a.u.' for a genuinely arbitrary unit.")


def units_are_interchangeable(source: str | None, target: str | None) -> bool:
    """Whether two axes may be related by a map that carries no numbers of its own.

    The write-time half of :func:`_pass_through_factor`, and deliberately the *same* rule read
    the other way round: that function answers "what is one unit of this axis worth on the other
    side", and this one answers "is that question allowed to have an answer other than 1". One
    predicate, two callers -- `assert_edge_rank` at write and `step_forms` at read -- because two
    copies of a unit rule is exactly the drift this module keeps warning about.

    ``True`` when neither side declares a unit (a pixel grid, where a unit would be meaningless),
    when they declare the same one, or when either declines to claim (``a.u.``) -- matching
    :func:`assert_unit_matches_type`, which short-circuits on the same string. ``False`` when the
    two are different real units, **whether or not a conversion exists**: micrometre facing
    nanometre is the dangerous case precisely *because* a factor exists, so an IDENTITY between
    them is a claim that one nanometre is one micrometre. Nanosecond facing micrometre is false
    for the blunter reason that no factor could exist.

    ``dimensionless`` is deliberately not an escape hatch: it is a real pint unit and a genuine
    claim, so a dimensionless axis facing a micrometre one really is a mismatch.
    """
    if not source or not target or source == target:
        return True
    if kanne_scalars.is_arbitrary_unit(source) or kanne_scalars.is_arbitrary_unit(target):
        return True
    try:
        return kanne_scalars.normalize_compact_units(source) == kanne_scalars.normalize_compact_units(target)
    except Exception:
        # An unparseable unit is not this function's error to raise -- `assert_unit_matches_type`
        # owns that, and raising a second one here would report the wrong problem.
        return source == target


def assert_at_most_one_time_axis(axes: Sequence[AxisSpec]) -> None:
    """Enforce that a coordinate system has at most one time axis.

    Two time axes are legal today -- they have equal ordering rank, so nothing
    complains -- and `resolve_render_axes` silently picks the first and drops the
    second. A scene with two clocks has no meaning; it just renders one of them.
    """
    time_axes = [axis.name for axis in axes if axis.type == enums.AxisTypeChoices.TIME.value]
    if len(time_axes) > 1:
        raise AxisOrderError(f"A coordinate system may carry at most one TIME axis, but was given {time_axes}")


def spatial_axes(axes: Sequence[AxisSpec]) -> list[AxisSpec]:
    """The spatial axes, in array order (slowest-varying first, i.e. z, y, x)."""
    return [axis for axis in axes if axis.type == enums.AxisTypeChoices.SPACE.value]


#: The spatial spec each SPACE-axis count denotes. Any count past the table collapses
#: to a single HYPERVOLUME at write time, so the open-ended `>=` is resolved here rather
#: than at query time.
_SPATIAL_SPEC_BY_COUNT: dict[int, enums.ArrayDatasetSpec] = {
    0: enums.ArrayDatasetSpec.SCALAR,
    1: enums.ArrayDatasetSpec.PROFILE,
    2: enums.ArrayDatasetSpec.IMAGE,
    3: enums.ArrayDatasetSpec.VOLUME,
}

#: The spec each non-spatial axis type denotes, when the axis has more than one position
#: (see :func:`specs_for_axes`). The acquisition axes come first, then the ones that say
#: what an array's *values* or positions are (DISPLACEMENT, COORDINATE, INDEX). New
#: members go at the end: the order here is the order of a stored spec, which a client
#: may compare by equality. VALUE is absent because no dataset's grid has one.
_SPEC_BY_AXIS_TYPE: dict[str, enums.ArrayDatasetSpec] = {
    enums.AxisTypeChoices.TIME.value: enums.ArrayDatasetSpec.TIMESERIES,
    enums.AxisTypeChoices.CHANNEL.value: enums.ArrayDatasetSpec.MULTICHANNEL,
    enums.AxisTypeChoices.SPECTRUM.value: enums.ArrayDatasetSpec.SPECTRAL,
    enums.AxisTypeChoices.MICROTIME.value: enums.ArrayDatasetSpec.FLIM,
    enums.AxisTypeChoices.DISPLACEMENT.value: enums.ArrayDatasetSpec.DISPLACEMENT_FIELD,
    enums.AxisTypeChoices.COORDINATE.value: enums.ArrayDatasetSpec.COORDINATE_FIELD,
    enums.AxisTypeChoices.INDEX.value: enums.ArrayDatasetSpec.INDEXED,
}

#: The axis types positions are measured along: the ones where the distance between two
#: positions means something. What a curve is read along, and what a chart is laid out along.
#: The same members as `_DOWNSAMPLABLE_TYPES` today, and a different question: a type a
#: pyramid stops striding would still be measured.
METRIC_AXIS_TYPES: frozenset[str] = frozenset(
    {
        enums.AxisTypeChoices.SPACE.value,
        enums.AxisTypeChoices.TIME.value,
        enums.AxisTypeChoices.MICROTIME.value,
        enums.AxisTypeChoices.SPECTRUM.value,
    }
)

#: The axis types whose positions are members rather than measurements. A curve may run
#: one line per position of one of these: a line per channel, a line per object.
ENUMERATING_AXIS_TYPES: frozenset[str] = frozenset(
    {
        enums.AxisTypeChoices.CHANNEL.value,
        enums.AxisTypeChoices.INDEX.value,
    }
)


def _is_trace(extended: Sequence[AxisSpec]) -> bool:
    """Whether the axes a dataset has extent along make it a curve: values read along one measured axis.

    Exactly one metric axis -- the one the values are read along -- and beside it at most
    one enumerating axis, one line per position. A second metric axis is a surface, and a
    value axis (COORDINATE, DISPLACEMENT) makes each sample a vector rather than a height.

    This is the shape a chart draws as a TRACE when it is handed the whole dataset, and the
    chart's own gate reads the same two sets. Whether a particular chart can draw it is a
    question of placement, which no spec answers.
    """
    measured = [axis for axis in extended if axis.type in METRIC_AXIS_TYPES]
    beside = [axis for axis in extended if axis.type not in METRIC_AXIS_TYPES]
    return len(measured) == 1 and len(beside) <= 1 and all(axis.type in ENUMERATING_AXIS_TYPES for axis in beside)


def specs_for_axes(axes: Sequence[AxisSpec], shape: Sequence[int]) -> list[enums.ArrayDatasetSpec]:
    """Every spec these axes satisfy at this shape: the one spatial member, then a modifier per non-spatial axis type, then what the whole amounts to.

    **An axis counts only when it has more than one position.** A z axis of a single plane
    does not make a stack, and a time axis of a single frame does not make a timelapse: a
    23 x 13 image stored as ``(z=1, y=13, x=23)`` is an IMAGE, not a VOLUME. The spec says
    what the data *is*; that an axis is declared at all is what the ``hasAxisTypes`` filter
    answers.

    ``shape`` is the dataset's level-0 shape, one extent per axis in the same order.

    The spatial member comes first and the modifiers follow in a fixed order, so
    the list is deterministic and a client may compare it by equality.

    This is the single source of truth for a dataset's spec: ``stored_spec`` is
    materialized *from* it at creation (by :func:`core.logic.graph.create_pixel_axes`)
    and the `respec_datasets` job reads it too, so the derivation lives here once and
    the stored column can never disagree with it.
    """
    if len(shape) != len(axes):
        raise ValueError(f"{len(axes)} axes for a {len(shape)}-dimensional shape: a dataset's spec is read off its axes and their extents together.")

    extended = [axis for axis, extent in zip(axes, shape) if extent > 1]
    count = len(spatial_axes(extended))
    specs = [_SPATIAL_SPEC_BY_COUNT.get(count, enums.ArrayDatasetSpec.HYPERVOLUME)]
    present = {axis.type for axis in extended}
    specs.extend(spec for axis_type, spec in _SPEC_BY_AXIS_TYPE.items() if axis_type in present)
    if _is_trace(extended):
        specs.append(enums.ArrayDatasetSpec.TRACE)
    return specs


@dataclass(frozen=True)
class ColumnSpec:
    """The subset of a declared table column a table's spec is read off, so the derivation never touches the ORM.

    ``Column`` rows and the create path's declaration both coerce into this.
    """

    name: str
    role: str
    axis_type: str | None
    dtype: str
    #: Whether the column's values identify rows of another table (`Column.references`).
    references: bool = False
    #: The network collection whose node ids the column holds (`Column.node_references`), or None.
    node_references: int | None = None


#: The placement spec each SPACE-coordinate-column count denotes. Any count past the table
#: collapses to POINTS_ND, as an array's collapses to HYPERVOLUME.
_PLACEMENT_SPEC_BY_COUNT: dict[int, enums.TableDatasetSpec] = {
    0: enums.TableDatasetSpec.RECORDS,
    1: enums.TableDatasetSpec.POINTS_1D,
    2: enums.TableDatasetSpec.POINTS_2D,
    3: enums.TableDatasetSpec.POINTS_3D,
}

#: The placement specs a point or a track layer draws: at least two SPACE coordinate columns.
POINT_TABLE_SPECS: frozenset[enums.TableDatasetSpec] = frozenset(
    {enums.TableDatasetSpec.POINTS_2D, enums.TableDatasetSpec.POINTS_3D, enums.TableDatasetSpec.POINTS_ND}
)

#: The DuckDB type names that are numbers. A DECIMAL carries its precision in the name, so
#: it is matched by prefix.
_NUMERIC_DTYPES: frozenset[str] = frozenset(
    {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "REAL", "DOUBLE"}
)


def is_numeric_dtype(dtype: str) -> bool:
    """Whether a column's stored type, a DuckDB type name, is a number."""
    dtype = dtype.upper()
    return dtype in _NUMERIC_DTYPES or dtype.startswith("DECIMAL")


def specs_for_columns(columns: Sequence[ColumnSpec], pinned: Iterable[str] = ()) -> list[enums.TableDatasetSpec]:
    """Every spec these declared columns satisfy: the one placement member, then a modifier per declared fact, then what the whole amounts to.

    ``pinned`` names the coordinate columns a lens' windows hold to a single value
    (:func:`pinned_axes`). It is the one way a selection changes what a table is, and it is
    the window form of an array's rule: a coordinate with one position does not count --
    toward the placement member, toward TIMED, or toward the one measured coordinate a SERIES
    is laid along. A window that only bounds changes nothing: the rows it keeps still spread
    along that axis, as far as anyone has declared. Empty for the table itself.

    **Declared, not measured.** :func:`specs_for_axes` counts an axis only when it has more
    than one position; this cannot, because the server never scans a table: there is no row
    count and no column's range to read. A TIME coordinate column makes a table TIMED
    whether its rows span an hour or a single instant.

    The placement member comes first and the modifiers follow in a fixed order, so the list
    is deterministic and a client may compare it by equality. New members go at the end of
    their group.

    This is the single source of truth for a table's spec: ``stored_spec`` is materialized
    *from* it by ``create_table_dataset``, and the layer gates read what was stored. A table
    lens' spec is this function over the same columns and its own pins, materialized when the lens is made.
    """
    held = set(pinned)
    coordinates = [column for column in columns if column.role == enums.ColumnRoleChoices.COORDINATE.value and column.name not in held]
    spatial = sum(1 for column in coordinates if column.axis_type == enums.AxisTypeChoices.SPACE.value)
    roles = {column.role for column in columns}
    per_collection: dict[int, int] = {}
    for column in columns:
        if column.node_references is not None:
            per_collection[column.node_references] = per_collection.get(column.node_references, 0) + 1
    node_columns = set(per_collection.values())

    specs = [_PLACEMENT_SPEC_BY_COUNT.get(spatial, enums.TableDatasetSpec.POINTS_ND)]
    if any(column.axis_type == enums.AxisTypeChoices.TIME.value for column in coordinates):
        specs.append(enums.TableDatasetSpec.TIMED)
    if enums.ColumnRoleChoices.TRACK_ID.value in roles:
        specs.append(enums.TableDatasetSpec.TRACKED)
    if enums.ColumnRoleChoices.GROUP_ID.value in roles:
        specs.append(enums.TableDatasetSpec.GROUPED)
    if any(column.references for column in columns):
        specs.append(enums.TableDatasetSpec.KEYED)
    if 1 in node_columns:
        specs.append(enums.TableDatasetSpec.NODE_TABLE)
    if 2 in node_columns:
        specs.append(enums.TableDatasetSpec.EDGE_TABLE)

    measured = sum(1 for column in coordinates if column.axis_type in METRIC_AXIS_TYPES)
    has_value = any(column.role == enums.ColumnRoleChoices.ATTRIBUTE.value and is_numeric_dtype(column.dtype) for column in columns)
    if measured == 1 and has_value:
        specs.append(enums.TableDatasetSpec.SERIES)
    return specs


def pinned_axes(windows: Iterable["WindowLike"]) -> list[str]:
    """The axes these windows hold to a single value: ``min == max``. The window form of an extent of one."""
    return [window.axis for window in windows if window.min is not None and window.min == window.max]


#: The rank spec each count of free axes denotes. Any count past the table collapses to TENSOR.
_SPARSE_SPEC_BY_RANK: dict[int, enums.SparseDatasetSpec] = {
    0: enums.SparseDatasetSpec.SCALAR,
    1: enums.SparseDatasetSpec.VECTOR,
    2: enums.SparseDatasetSpec.MATRIX,
}


def is_sparse_slice(axis_names: Sequence[str], shape: Sequence[int], *, identified: Iterable[str], indexed: Iterable[str], windows: Sequence["WindowLike"]) -> bool:
    """Whether these windows cut one slice of a sparse matrix: what a SPARSE picker entry reads.

    One whole-number position, inside the axis' extent, on every axis the matrix identifies
    itself by and on no other -- the remaining axis is the one a source supplies ids for --
    and a layout indexing one of the pinned axes, so the read is one contiguous range rather
    than a scan of the store.
    """
    windowed = {window.axis: window for window in windows}
    if not windowed or set(windowed) != set(identified):
        return False
    known = len(shape) == len(axis_names)
    for axis, window in windowed.items():
        if window.min is None or window.min != window.max or window.min != int(window.min):
            return False
        if known and axis in axis_names and not 0 <= int(window.min) < shape[list(axis_names).index(axis)]:
            return False
    return bool(set(windowed) & set(indexed))


def specs_for_sparse(
    axis_names: Sequence[str],
    shape: Sequence[int],
    *,
    identified: Iterable[str] = (),
    indexed: Iterable[str] = (),
    windows: Sequence["WindowLike"] = (),
) -> list[enums.SparseDatasetSpec]:
    """Every spec a sparse matrix, or these windows over it, satisfies: the one rank member, then SLICE when a picker can read it.

    An axis counts when it has more than one position and no window pins it. ``shape`` is the
    store's own; when it does not match the axes (a store that never recorded one) no extent
    is known and every unpinned axis counts. ``identified`` are the axes that reference a
    table, ``indexed`` the ones a stored layout makes contiguous.
    """
    known = len(shape) == len(axis_names)
    held = set(pinned_axes(windows))
    free = [name for index, name in enumerate(axis_names) if name not in held and (not known or shape[index] > 1)]
    specs = [_SPARSE_SPEC_BY_RANK.get(len(free), enums.SparseDatasetSpec.TENSOR)]
    if is_sparse_slice(axis_names, shape, identified=identified, indexed=indexed, windows=windows):
        specs.append(enums.SparseDatasetSpec.SLICE)
    return specs


def array_to_vertex_order(coords: Sequence[float], axes: Sequence[AxisSpec]) -> list[float]:
    """Permute spatial coordinates from array order (z, y, x) to vertex order (x, y, z).

    THE permutation. Array order is slowest-varying first, matching a numpy shape
    tuple; vertex order is what a GPU expects. They are reverses of each other,
    and every inlined copy of this reversal is a place where it can be forgotten
    in one direction only.

    ``coords`` is indexed like the full axis list; only the spatial entries are
    returned, reversed.
    """
    spatial_indices = [index for index, axis in enumerate(axes) if axis.type == enums.AxisTypeChoices.SPACE.value]
    return [coords[index] for index in reversed(spatial_indices)]


def vertex_to_array_order(vertex: Sequence[float], axes: Sequence[AxisSpec]) -> dict[str, float]:
    """The inverse of :func:`array_to_vertex_order`: map (x, y, z) back onto named array axes."""
    spatial = spatial_axes(axes)
    if len(vertex) != len(spatial):
        raise ValueError(f"Expected {len(spatial)} vertex components for spatial axes {[axis.name for axis in spatial]}, got {len(vertex)}")
    return {axis.name: value for axis, value in zip(reversed(spatial), vertex, strict=True)}


#: The screen axes, named. A spatial axis set that is exactly one of these is bound by name;
#: anything else falls back to position. Two entries and three, and nothing in between: a
#: partial match is the case that must *not* bind, because then one axis is chosen by name and
#: its neighbour by position and the two can claim the same slot.
_SCREEN_AXIS_NAMES: tuple[frozenset[str], ...] = (frozenset({"x", "y"}), frozenset({"x", "y", "z"}))


def resolve_render_axes(axes: Sequence[AxisSpec]) -> RenderAxes:
    """Derive the x / y / z / time / intensity axis names a renderer needs.

    **Spatial axes are bound by name when they are named for the screen, and by position
    otherwise.** If the spatial axes are exactly ``{x, y}`` or exactly ``{x, y, z}``, each one
    is the axis it is called. Otherwise the array convention applies: the **last** spatial axis
    is x, the second-to-last is y, the third-to-last is z.

    Position alone cannot answer this, and said so here for a long time: ``(z, y, x)`` and
    ``(x, y, z)`` are both well-formed, only one is meant, and the second derived ``x=z, z=x``
    -- transposed, silently, on both sides. Nothing raised because nothing could: the
    derivation had no evidence to raise *on*. A name that follows the convention is that
    evidence, and it is the same evidence `core.types.array_dataset._coordinate_column_named`
    already uses for a table's coordinate columns, for the same stated reason.

    **All-or-nothing, and that is the whole subtlety.** A set like ``(x, y, q)`` matches neither
    entry of `_SCREEN_AXIS_NAMES` and falls back *wholly* to position. Binding the two it
    recognises and leaving ``q`` positional would let ``q`` and ``x`` both claim x -- a worse
    failure than the one being fixed, because it would be inconsistent rather than merely
    conventional.

    Only the **spatial** axes are in question here. The time, channel, phasor and vector axes
    are found by a type scan and always were: where they sit among the others changes nothing, which is
    why no system's axes are held to a type ordering at all -- see the note at the top of this
    module, and :func:`core.logic.graph.create_table_axes`, which reasoned it out first.
    """
    spatial = spatial_axes(axes)
    if len(spatial) < 2:
        raise ValueError(f"A renderable coordinate system needs at least two spatial axes, got {[axis.name for axis in spatial]}")

    by_name = {axis.name.lower(): axis.name for axis in spatial}
    if len(by_name) == len(spatial) and frozenset(by_name) in _SCREEN_AXIS_NAMES:
        x, y, z = by_name["x"], by_name["y"], by_name.get("z")
    else:
        x, y = spatial[-1].name, spatial[-2].name
        z = spatial[-3].name if len(spatial) >= 3 else None

    return RenderAxes(
        x=x,
        y=y,
        z=z,
        t=next((axis.name for axis in axes if axis.type == enums.AxisTypeChoices.TIME.value), None),
        intensity=next((axis.name for axis in axes if axis.type == enums.AxisTypeChoices.CHANNEL.value), None),
        phasor=next((axis.name for axis in axes if axis.type in _PHASOR_TYPES), None),
        # DISPLACEMENT only, deliberately: a COORDINATE value axis holds absolute positions --
        # a lookup table, a warp target -- which is a map to follow, not an offset to draw.
        vector=next((axis.name for axis in axes if axis.type == enums.AxisTypeChoices.DISPLACEMENT.value), None),
    )


def is_renderable(axes: Sequence[AxisSpec], axis_names: Sequence[str], shape: Sequence[int]) -> bool:
    """Whether data of this shape can be drawn at all: an x and a y axis of more than one pixel.

    The boolean form of the condition ``core.mutations.layer.assert_renderable`` raises on,
    factored out so a *batch* -- a scene builder skipping one bad source, a picker deciding
    what to offer -- can ask it without an exception per candidate. It takes the axes and
    the shape rather than a model so it serves a dataset and a lens alike: pass the lens'
    :func:`lens_shape` and a slice cropping x to a single column is caught, which is the
    whole reason the lens is the granularity a picker must ask about.

    Too few spatial axes is not renderable either, rather than the ValueError
    :func:`resolve_render_axes` raises: "this cannot be drawn" is exactly the answer, and a
    caller filtering a list has nothing to do with the exception but swallow it.
    """
    try:
        render = resolve_render_axes(axes)
    except ValueError:
        return False

    def size(axis: str | None) -> int:
        return shape[axis_names.index(axis)] if axis is not None and axis in axis_names and axis_names.index(axis) < len(shape) else 0

    return size(render.x) > 1 and size(render.y) > 1


def is_phasor_axis(axis_type: str) -> bool:
    """Whether a phasor may be taken over an axis of this type."""
    return axis_type in _PHASOR_TYPES


def pyramid_transform(
    shape_0: Sequence[int],
    shape_level: Sequence[int],
    axes: Sequence[AxisSpec],
) -> tuple[list[float], list[float]]:
    """The scale and translation taking a pyramid level into its dataset's intrinsic pixel space.

    Absolute, not relative -- and dimensionless. A real pyramid does not halve
    cleanly: a 36-voxel z axis floors to 36, 18, 9, 4, 2, 1, so its true factors
    are 1, 2, 4, **9, 18, 36** -- while a nominal ``2 ** level`` claims 1, 2, 4,
    8, 16, 32. Levels 3 and up are compressed in z, and a model that stores the
    nominal factors has no way to say so. It stays invisible for as long as every
    axis happens to be a power of two, which is why xy never showed it.

    The translation is the half-voxel offset a downsample introduces: level 0's
    voxel centres sit at 0, 1, 2, ... while level 1's sit at 0.5, 2.5, ... in
    level-0 coordinates. Without it, every level above 0 draws offset from level 0.

    Every level's output is the *same* intrinsic system -- a star, not a chain.
    Physical units never enter here: they are their own edge off the
    intrinsic system, so refining it cannot move the pyramid.

    Only *continuous* axes (space, time, microtime) may be downsampled -- a
    temporal pyramid over a long timelapse and a re-binned FLIM axis are as
    meaningful as spatial downsampling. Categorical axes (channel, coordinate,
    displacement) must keep their extent: c=0.5 between two channels is nonsense.
    """
    if not len(shape_0) == len(shape_level) == len(axes):
        raise ValueError(f"shape_0 ({len(shape_0)}), shape_level ({len(shape_level)}) and axes ({len(axes)}) must agree in length")

    scale: list[float] = []
    translation: list[float] = []

    for index, axis in enumerate(axes):
        if shape_level[index] == 0:
            raise ValueError(f"Level shape is 0 along axis '{axis.name}'")

        # Kept a float on purpose: a ceil-downsampled pyramid gives 33 -> 17, a
        # factor of 1.941..., and rounding it is exactly the bug this replaces.
        factor = shape_0[index] / shape_level[index]

        if axis.type not in _DOWNSAMPLABLE_TYPES and factor != 1:
            raise ValueError(f"Axis '{axis.name}' is of categorical type {axis.type} and must not be downsampled (shape {shape_0[index]} -> {shape_level[index]}). A fractional coordinate between two categories is meaningless.")

        scale.append(factor)
        translation.append((factor - 1) / 2)

    return scale, translation


def lens_shape(dataset_shape: Sequence[int], dataset_axis_names: Sequence[str], slices: Iterable) -> list[int]:
    """The shape a lens' slices cut out of its dataset.

    Uses Python's own slice semantics, so negatives, omitted bounds and
    out-of-range stops resolve exactly as they would on the array itself.
    """
    by_axis = {slice_.axis: slice_ for slice_ in slices}
    shape: list[int] = []

    for axis, size in zip(dataset_axis_names, dataset_shape, strict=True):
        selection = by_axis.get(axis)
        if selection is None:
            shape.append(size)
            continue
        start, stop, step = slice(selection.start, selection.stop, selection.step).indices(size)
        shape.append(len(range(start, stop, step)))

    return shape


def lens_voxel_box(dataset_shape: Sequence[int], dataset_axis_names: Sequence[str], slices: Iterable) -> tuple[list[float], list[float]]:
    """The box of the voxels a lens keeps, in the lens' own space, half a voxel around each centre.

    Lens voxel ``i`` is dataset voxel ``start + i·step``, and that voxel's cell -- half a dataset
    voxel either side of its centre -- is ``[i - 0.5/step, i + 0.5/step]`` once pulled back
    through the lens' edge. So a stepped lens' box is ``[-0.5/step, (n - 1) + 0.5/step]`` per
    axis: through ``SCALE(step)·TRANSLATION(start)`` that is ``[start - 0.5, start + (n-1)·step + 0.5]``
    in the dataset, the footprint of the voxels it keeps. Pushing the plain ``[-0.5, n - 0.5]``
    box instead would claim ``n·step`` voxels, half a step too wide at either end. At step 1 the
    two agree, so an unstepped lens' box is the ``[-0.5, S - 0.5]`` of every other resident.
    """
    by_axis = {slice_.axis: slice_ for slice_ in slices}
    shape = lens_shape(dataset_shape, dataset_axis_names, by_axis.values())
    mins: list[float] = []
    maxs: list[float] = []
    for axis, size in zip(dataset_axis_names, shape, strict=True):
        selection = by_axis.get(axis)
        step = float(selection.step or 1) if selection is not None else 1.0
        mins.append(-0.5 / step)
        maxs.append((size - 1) + 0.5 / step)
    return mins, maxs


def normalize_slices(dataset_shape: Sequence[int], dataset_axis_names: Sequence[str], slices: Iterable["SliceLike"]) -> list[dict[str, int | str]]:
    """The one spelling of a selection: a lens' slices resolved against its dataset.

    A lens is a deterministic function of what it selects, so two callers asking for the
    same voxels must produce the same row -- and ``[-56:]``, ``[8:]``, ``[8:64]`` and
    ``[8:400]`` over a 64-pixel axis are one selection spelled four ways. This resolves
    every spelling to the one form that is stored and looked up:

    * bounds resolved against the dataset's level-0 shape with Python's own slice
      semantics (:meth:`slice.indices`), so negatives, omitted bounds and out-of-range
      stops land where they would on the array itself. The shape is written once, at
      creation, and never after, which is what makes resolving an open bound safe;
    * ``stop`` is the first index past the last *selected* position, so a stepped slice
      with a ragged stop (``0:10:3`` and ``0:12:3`` both pick 0, 3, 6, 9) spells the same;
    * a slice that keeps a whole axis at step 1 is dropped: it selects nothing narrower
      than the array, and a lens that carries one would be a second whole lens;
    * dataset axis order, explicit ints, all four keys, ``step`` always present.

    Refused, in prose: an axis the dataset does not have, an axis sliced twice, a step of
    zero or less (a lens narrows an axis, it never reverses one -- and the stop of a reversed
    slice has no non-negative spelling), and a selection that is empty along an axis.
    """
    by_axis: dict[str, SliceLike] = {}
    for selection in slices:
        if selection.axis not in dataset_axis_names:
            raise ValueError(f"Slice names axis '{selection.axis}', which the dataset does not have (its axes are {list(dataset_axis_names)})")
        if selection.axis in by_axis:
            raise ValueError(f"Axis '{selection.axis}' is sliced twice; one slice per axis")
        by_axis[selection.axis] = selection

    normalized: list[dict[str, int | str]] = []
    for axis, size in zip(dataset_axis_names, dataset_shape, strict=True):
        selection = by_axis.get(axis)
        if selection is None:
            continue
        step = selection.step if selection.step is not None else 1
        if step <= 0:
            raise ValueError(f"Slice over axis '{axis}' has step {step}; a lens narrows an axis, it does not reverse it, so the step must be positive")
        start, stop, step = slice(selection.start, selection.stop, step).indices(size)
        count = len(range(start, stop, step))
        if count == 0:
            raise ValueError(f"Slice over axis '{axis}' ({selection.start}:{selection.stop}:{selection.step}) selects nothing of its {size} positions")
        stop = start + (count - 1) * step + 1
        if start == 0 and stop == size and step == 1:
            continue
        normalized.append({"axis": axis, "start": start, "stop": stop, "step": step})

    return normalized


class WindowLike(Protocol):
    """What this module reads off a window: a pydantic ``WindowModel``, a GraphQL input, a test stub."""

    axis: str
    min: float | None
    max: float | None


def normalize_windows(axis_names: Sequence[str], windows: Iterable["WindowLike"]) -> list[dict[str, float | str | None]]:
    """The one spelling of a value selection over a container that is not an array.

    A window keeps the positions along an axis whose value lies in ``[min, max]``; it
    re-indexes nothing, so unlike :func:`normalize_slices` there is no shape to resolve
    against and an open side stays open (``None``). What is normalized is the spelling:
    floats, container axis order, one window per axis, and a window open on both sides
    dropped -- it keeps everything, and a lens that carried one would be a second whole lens.

    Refused, in prose: an axis the container does not have, an axis windowed twice, and a
    window whose ``min`` is not below its ``max`` (an empty window selects nothing).
    """
    by_axis: dict[str, WindowLike] = {}
    for window in windows:
        if window.axis not in axis_names:
            raise ValueError(f"Window names axis '{window.axis}', which the container does not have (its axes are {list(axis_names)})")
        if window.axis in by_axis:
            raise ValueError(f"Axis '{window.axis}' is windowed twice; one window per axis")
        by_axis[window.axis] = window

    normalized: list[dict[str, float | str | None]] = []
    for axis in axis_names:
        window = by_axis.get(axis)
        if window is None:
            continue
        low = None if window.min is None else float(window.min)
        high = None if window.max is None else float(window.max)
        if low is None and high is None:
            continue
        # A window is inclusive on both sides, so `[v, v]` keeps the positions at `v`: one
        # channel, one feature, one timepoint. Only a window whose min lies above its max is empty.
        if low is not None and high is not None and low > high:
            raise ValueError(f"Window over axis '{axis}' runs from {low} to {high} and keeps nothing; its min must not exceed its max")
        normalized.append({"axis": axis, "min": low, "max": high})

    return normalized


def lens_to_parent(dataset_axis_names: Sequence[str], slices: Iterable) -> tuple[str, dict]:
    """The edge from a lens' coordinate system to its dataset's level-0 array system.

    This closes a live correctness hole: slicing shifts voxel coordinates and
    nothing recorded the shift, so an ROI drawn on a cropped lens had no defined
    path back to the dataset it came from.

    A pure crop is a ``TRANSLATION`` of the slice starts. A *stepped* lens also
    rescales, so it is a ``SEQUENCE[SCALE(step), TRANSLATION(start)]`` -- a
    translation-only edge would mis-place every subsampled lens, and would do it
    without complaining.

    Returns the kind and the params for the edge; the caller builds the rows.
    """
    by_axis = {slice_.axis: slice_ for slice_ in slices}

    starts = [float(by_axis[axis].start or 0) if axis in by_axis else 0.0 for axis in dataset_axis_names]
    steps = [float(by_axis[axis].step or 1) if axis in by_axis else 1.0 for axis in dataset_axis_names]

    if all(step == 1 for step in steps):
        return enums.TransformKindChoices.TRANSLATION.value, {"translation": starts}

    return enums.TransformKindChoices.SEQUENCE.value, {"scale": steps, "translation": starts}


# --- affine composition, for the derived ROI bounding box -------------------


def identity_matrix(n: int) -> list[list[float]]:
    """An (n+1) x (n+1) homogeneous identity matrix."""
    return [[1.0 if row == col else 0.0 for col in range(n + 1)] for row in range(n + 1)]


def matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Multiply two homogeneous matrices."""
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


#: How close to zero a pivot may be, relative to the matrix's largest entry, before the
#: matrix counts as singular -- which is to say, the reciprocal of the worst condition number
#: worth trusting. 1e-12 against float64's ~2.2e-16 epsilon leaves four digits: a map whose
#: axes differ by more than twelve orders of magnitude cannot be inverted to anything a
#: client should act on, so refusing it is the honest answer rather than a conservative one.
#:
#: Clamped at 1 from below at the point of use, so a map whose every entry is small -- one
#: stated in nanometres -- is not called singular merely for being small.
_SINGULAR_TOLERANCE = 1e-12


def is_singular(matrix: Sequence[Sequence[float]]) -> bool:
    """Whether a square matrix has no usable inverse.

    Deliberately **not** a determinant. A determinant answers "is this exactly singular",
    which is not the question a float64 matrix can be asked: a map whose determinant is
    ``1.0`` may still be uninvertible in practice if its two axes differ by fourteen orders
    of magnitude, and one whose determinant is ``1e-14`` may be a perfectly good map stated
    in nanometres. The first would be waved through and the second refused -- the wrong
    answer in both directions.

    So this asks the elimination itself, by running :func:`invert_matrix` and reporting
    whether it could. One definition of singular, computed one way, so the write-time gate
    and the read-time composer cannot come to different conclusions about the same numbers.
    """
    try:
        invert_matrix(matrix)
    except SingularTransformError:
        return True
    return False


def invert_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """The inverse of a square homogeneous matrix, by Gauss-Jordan with partial pivoting.

    Raises :class:`SingularTransformError` when a pivot is within tolerance of zero -- which
    is the one failure mode a caller has to handle, because `is_invertible` decides
    invertibility by *kind* and so offers a singular AFFINE for inversion (its own docstring
    says as much). Partial pivoting is not an optimisation: without it a matrix with a zero
    on the diagonal -- an axis swap, which is an ordinary registration -- fails to invert
    despite being perfectly invertible.
    """
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError(f"Only a square matrix has an inverse, but this one is {size} x {[len(row) for row in matrix]}")

    rows = [[float(value) for value in row] for row in matrix]
    # `identity_matrix` takes a *rank* and returns (n+1) x (n+1), being written for
    # homogeneous coordinates; here the argument is a plain size, so it is one less.
    result = identity_matrix(size - 1) if size else []
    largest = max((abs(value) for row in rows for value in row), default=0.0)
    tolerance = _SINGULAR_TOLERANCE * max(1.0, largest)

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda index: abs(rows[index][column]))
        if abs(rows[pivot_row][column]) <= tolerance:
            raise SingularTransformError(
                f"This map has no inverse: its matrix is singular (it collapses at least one axis), so walking it backwards would ask for a point where there is a whole line. Matrix: {matrix}"
            )
        if pivot_row != column:
            rows[column], rows[pivot_row] = rows[pivot_row], rows[column]
            result[column], result[pivot_row] = result[pivot_row], result[column]

        pivot = rows[column][column]
        rows[column] = [value / pivot for value in rows[column]]
        result[column] = [value / pivot for value in result[column]]

        for index in range(size):
            if index == column:
                continue
            factor = rows[index][column]
            if factor == 0.0:
                continue
            rows[index] = [value - factor * other for value, other in zip(rows[index], rows[column])]
            result[index] = [value - factor * other for value, other in zip(result[index], result[column])]

    return result


#: The kinds :func:`to_matrix` can write as one fixed-rank homogeneous matrix.
#:
#: A SEQUENCE is here on the strength of the only shape anything writes: the scale-then-
#: translate pair :func:`lens_to_parent` emits. It is not directly creatable through the API
#: (see ``_PARAMS_BY_KIND``), so there is no user-authored SEQUENCE carrying something the
#: branch below would quietly ignore.
MATRIX_KINDS = frozenset(
    {
        enums.TransformKindChoices.IDENTITY.value,
        enums.TransformKindChoices.SCALE.value,
        enums.TransformKindChoices.TRANSLATION.value,
        enums.TransformKindChoices.MAP_AXIS.value,
        enums.TransformKindChoices.AFFINE.value,
        enums.TransformKindChoices.ROTATION.value,
        enums.TransformKindChoices.SEQUENCE.value,
    }
)


def has_matrix(kind: str) -> bool:
    """Whether :func:`to_matrix` has an answer for this kind, asked *before* composing.

    A walk that composes a chain has to know where to stop, and finding out by catching the
    raise is too late: by then the chain is half-built and the caller is left choosing between
    a partial composition and none. Pinned against :func:`to_matrix` by test, because a new
    kind is exactly the sort of thing that gets a branch there and not an entry here.
    """
    return kind in MATRIX_KINDS


def to_matrix(kind: str, params: dict, n: int) -> list[list[float]]:
    """The homogeneous matrix of an affine transformation kind.

    Raises :class:`NonAffineTransformError` for the kinds that have no matrix -- ask
    :func:`has_matrix` first if you need to know without the raise.
    Selection stays correct under those anyway -- the ROI box is pushed *forward*,
    and only vertex placement needs the forward map, which is always available.
    """
    matrix = identity_matrix(n)

    if kind == enums.TransformKindChoices.IDENTITY.value:
        return matrix

    if kind == enums.TransformKindChoices.SCALE.value:
        for i, value in enumerate(params["scale"]):
            matrix[i][i] = float(value)
        return matrix

    if kind == enums.TransformKindChoices.TRANSLATION.value:
        for i, value in enumerate(params["translation"]):
            matrix[i][n] = float(value)
        return matrix

    if kind in (enums.TransformKindChoices.AFFINE.value, enums.TransformKindChoices.ROTATION.value, enums.TransformKindChoices.MAP_AXIS.value):
        # MAP_AXIS is a permutation, which is an affine map -- but its permutation lives in
        # the edge's `inputAxes`/`outputAxes` *columns*, not in `params`, and this function
        # only ever sees params. `graph._edge_params` synthesizes the matrix from those two
        # lists, which is why this can be one branch instead of a signature change. Without
        # it, a MAP_AXIS anywhere on an ROI's chain raised, `compute_intrinsic_bbox` caught
        # the raise as "no chain", and the box came back in the wrong frame with an
        # intrinsic label on it.
        rows = params["affine"]
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                matrix[i][j] = float(value)
        return matrix

    if kind == enums.TransformKindChoices.SEQUENCE.value:
        # The SEQUENCE emitted by lens_to_parent: scale, then translate.
        if "scale" in params:
            matrix = matmul(to_matrix(enums.TransformKindChoices.SCALE.value, params, n), matrix)
        if "translation" in params:
            matrix = matmul(to_matrix(enums.TransformKindChoices.TRANSLATION.value, params, n), matrix)
        return matrix

    if kind == enums.TransformKindChoices.UNMAPPABLE.value:
        raise NonAffineTransformError("an UNMAPPABLE edge declares that no point of one space corresponds to a point of the other, so it has no matrix -- not an affine one, and not any other")

    if kind == enums.TransformKindChoices.BY_DIMENSION.value:
        raise NonAffineTransformError(
            "a BY_DIMENSION edge says nothing at all about the axes it does not name, and a fixed-rank matrix has no way to write that which is not a zero -- and a zero is a claim (see `_by_dimension_forms`). Compose it with `step_forms`, which is axis-keyed and can leave an axis out"
        )

    raise NonAffineTransformError(f"{kind} has no affine matrix")


def permutation_matrix(input_axes: list[str], output_axes: list[str], axis_order: list[str]) -> list[list[float]]:
    """The affine matrix of a MAP_AXIS edge, a permutation written out.

    ``axis_order`` is the order the coordinate vector is written in, so a coordinate at
    column `i` is the value of ``axis_order[i]``. The edge maps ``input_axes[k]`` onto
    ``output_axes[k]``; an axis it does not name is one it leaves where it was.
    """
    n = len(axis_order)
    mapping = dict(zip(input_axes, output_axes))

    matrix = [[0.0] * (n + 1) for _ in range(n + 1)]
    matrix[n][n] = 1.0

    for column, axis in enumerate(axis_order):
        target = mapping.get(axis, axis)
        if target not in axis_order:
            raise NonAffineTransformError(f"A MAP_AXIS edge maps '{axis}' onto '{target}', which is not one of the axes {axis_order} its coordinates are written in")
        matrix[axis_order.index(target)][column] = 1.0

    return matrix


def compose(edges: Sequence[tuple[str, dict]], n: int) -> list[list[float]]:
    """Compose a path of edges, applied first to last, into one homogeneous matrix."""
    matrix = identity_matrix(n)
    for kind, params in edges:
        matrix = matmul(to_matrix(kind, params, n), matrix)
    return matrix


def apply(matrix: list[list[float]], point: Sequence[float]) -> list[float]:
    """Apply a homogeneous matrix to a point."""
    n = len(point)
    return [sum(matrix[i][j] * point[j] for j in range(n)) + matrix[i][n] for i in range(n)]


def bbox_corners(mins: Sequence[float], maxs: Sequence[float]) -> list[list[float]]:
    """Every corner of an axis-aligned box: 2**n of them, so eight in 3D."""
    corners: list[list[float]] = [[]]
    for low, high in zip(mins, maxs, strict=True):
        corners = [corner + [bound] for corner in corners for bound in (low, high)]
    return corners


def aabb(points: Sequence[Sequence[float]]) -> tuple[list[float], list[float]]:
    """The axis-aligned bounding box of a set of points."""
    if not points:
        raise ValueError("Cannot take the bounding box of no points")
    return (
        [min(point[i] for point in points) for i in range(len(points[0]))],
        [max(point[i] for point in points) for i in range(len(points[0]))],
    )


def transformed_bbox(mins: Sequence[float], maxs: Sequence[float], edges: Sequence[tuple[str, dict]]) -> dict:
    """The bounding box of a box after a chain of transformations.

    **Transforms all the corners**, not just the two extremes. An affine-transformed
    AABB is not an AABB: under any rotation or shear, pushing only min and max
    through the matrix gives a box that is not merely different but *wrong* --
    strictly too small, so geometry that is really inside it tests as outside.
    """
    matrix = compose(edges, len(mins))
    corners = [apply(matrix, corner) for corner in bbox_corners(mins, maxs)]
    low, high = aabb(corners)
    return {"min": low, "max": high}


def vectors_bbox(vectors: Sequence[Sequence[float]]) -> tuple[list[float], list[float]]:
    """The half-open bounding box of an ROI's vertices.

    The voxel centre is the origin and voxel ``n`` covers ``[n - 0.5, n + 0.5)``,
    so a single-voxel ROI at 340 spans 339.5 to 340.5 -- not 340 to 340.
    """
    low, high = aabb(vectors)
    return [value - 0.5 for value in low], [value + 0.5 for value in high]


# --- axis-aware composition, for pushing a box across a registration ------------------
#
# Everything above composes at one fixed rank, which is exactly right inside a dataset --
# a level, a lens and a physical space all keep the dataset's axes -- and exactly wrong across
# a registration. A BY_DIMENSION edge (what `create_identity_registration` writes for every
# ordinary registration) names the axes it acts on, leaves the ones it does not name
# untouched *by name*, and says nothing whatever about target axes it never mentions.
# Those three cases are distinguishable only with the axis names in hand, which is why
# these carry names where `to_matrix` carries a rank.
#
# `to_matrix`, `compose` and `transformed_bbox` are deliberately NOT widened to cover this.
# They are on the annotation write path, where their stored results are compared against
# each other for years; a change there is a regression surface for every `bbox_cube` already
# in the database, to serve a read path that can afford to be its own thing.


@dataclass(frozen=True)
class AxedStep:
    """One edge of a path, with both endpoints' axis names -- what a rank-changing map needs.

    ``acts_on_input`` / ``acts_on_output`` are the *named subset* a BY_DIMENSION or MAP_AXIS
    edge acts on, and ``input_axes`` / ``output_axes`` are the endpoints' full orders. Both
    are needed and neither substitutes for the other: the subset says which axes the
    parameters apply to, the full orders say which of the rest pass through and which the
    edge never mentions at all.
    """

    kind: str
    params: dict
    input_axes: tuple[str, ...]
    output_axes: tuple[str, ...]
    acts_on_input: tuple[str, ...] | None = None
    acts_on_output: tuple[str, ...] | None = None
    #: A SEQUENCE's steps as ``(kind, params)`` pairs, applied first to last; empty for every
    #: other kind, a BY_DIMENSION's map being its ``params``.
    steps: tuple[tuple[str, dict], ...] = ()
    #: Each endpoint's units, positionally parallel to its axis names. Empty means "not
    #: supplied", which is what every in-memory step that predates units says, and it reads as
    #: "no conversion" -- the behaviour before there were units here at all. Carried on the
    #: *step* rather than on `AxisSpec`, which still says units never enter a derivation and
    #: is still right: what enters a derivation is a unit *pair*, which is a property of the
    #: edge that relates the two spaces, not of either axis alone.
    input_units: tuple[str | None, ...] = ()
    output_units: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class AxedForm:
    """One output axis as an affine functional of the source coordinates: ``sum(c_i * x_i) + k``."""

    coefficients: tuple[float, ...]
    constant: float


def _forms_from_matrix(matrix: list[list[float]], row_labels: Sequence[str], rank: int) -> dict[str, AxedForm]:
    """The rows of a homogeneous matrix as one labelled functional each."""
    return {
        label: AxedForm(coefficients=tuple(float(matrix[index][column]) for column in range(rank)), constant=float(matrix[index][rank]))
        for index, label in enumerate(row_labels)
        if index < len(matrix)
    }


def _identity_form(axis: str, input_axes: Sequence[str], factor: float = 1.0) -> AxedForm:
    """The functional that passes one input axis through, in the output axis' own unit."""
    return AxedForm(coefficients=tuple(factor if name == axis else 0.0 for name in input_axes), constant=0.0)


def _unit_of(names: Sequence[str], units: Sequence[str | None], axis: str) -> str | None:
    """One endpoint's unit for a named axis, or None when it has none or none were supplied."""
    if not units or axis not in names:
        return None
    index = list(names).index(axis)
    return units[index] if index < len(units) else None


def _pass_through_factor(step: "AxedStep", axis: str) -> float | None:
    """How much one unit of `axis` on the input side is worth on the output side.

    **Only for an axis the edge passes through**, which is the one place a unit pair is a
    *fact* rather than a convention. An axis a BY_DIMENSION does not name, or every axis of an
    IDENTITY, means "the same physical position, said twice" -- so if the two sides declare
    different units, saying it with the same number is a false claim: an IDENTITY between a
    nanometre axis and a micrometre one asserts that one nanometre is one micrometre.

    **Deliberately not applied to the kinds that carry numbers.** A SCALE, TRANSLATION, AFFINE
    or ROTATION states its own magnitudes, and whether the author already folded the 1000 into
    them is not knowable from the row. Converting there would double-count exactly the authors
    who got it right, and there is no evidence in the schema to tell them apart -- the same
    reason `assert_edge_rank` refuses to guess an AFFINE's axis names (proposals item 15, F6).

    Returns ``None`` when the two units have no conversion between them at all -- a nanosecond
    axis facing a micrometre one. That is not a pass-through and there is no number that makes
    it one, so the caller drops the form rather than inventing a factor: absent, not one, on
    the same reasoning as `_by_dimension_forms`' "absent is not zero".
    """
    source = _unit_of(step.input_axes, step.input_units, axis)
    target = _unit_of(step.output_axes, step.output_units, axis)
    if not source or not target or source == target:
        return 1.0
    # An arbitrary unit ("a.u.") is a *declined* calibration claim, not a conflicting one --
    # `assert_unit_matches_type` short-circuits on exactly this and checks nothing. Reading it
    # as an incompatible dimension here would make one function treat the same string as "no
    # claim" and the next as "these axes cannot be related", and would unplace data that is
    # merely uncalibrated. `dimensionless` is deliberately NOT in this branch: it is a real
    # pint unit and a genuine claim, and a dimensionless axis facing a micrometre one really
    # does have no conversion between them.
    if kanne_scalars.is_arbitrary_unit(source) or kanne_scalars.is_arbitrary_unit(target):
        return 1.0
    registry = kanne_scalars.get_registry()
    try:
        return float(registry.Quantity(1.0, kanne_scalars.normalize_compact_units(source)).to(kanne_scalars.normalize_compact_units(target)).magnitude)
    except Exception:
        return None


def _by_dimension_forms(step: AxedStep) -> dict[str, AxedForm]:
    """A BY_DIMENSION edge's functionals: its named axes mapped, the rest passed through by name.

    Three populations, and the third is the one that matters. The axes it *names* are mapped
    by its parameters. The axes it does not name but both systems have
    pass through unchanged -- that is the rule `edge_axis_names` and `assert_edge_rank`
    already state. Everything else -- an output axis the edge never mentions and the input
    does not have -- gets **no form at all**, because the edge genuinely says nothing about
    where the data sits along it. Absent is not zero: zero would pin a (c,y,x) dataset at
    z=0 in a (z,y,x) world and cull it out of every other slice.
    """
    acts_in = list(step.acts_on_input or ())
    acts_out = list(step.acts_on_output or ())
    rank = len(step.input_axes)

    forms: dict[str, AxedForm] = {}

    if acts_in and acts_out:
        sub = _sub_matrix(step)
        for row, out_axis in enumerate(acts_out):
            if row >= len(sub):
                continue
            coefficients = [0.0] * rank
            for column, in_axis in enumerate(acts_in):
                if in_axis in step.input_axes:
                    coefficients[step.input_axes.index(in_axis)] = float(sub[row][column])
            forms[out_axis] = AxedForm(coefficients=tuple(coefficients), constant=float(sub[row][len(acts_in)]))

    for out_axis in step.output_axes:
        if out_axis in forms or out_axis in acts_out:
            continue
        if out_axis in step.input_axes and out_axis not in acts_in:
            factor = _pass_through_factor(step, out_axis)
            if factor is None:
                # The two sides give this axis units with no conversion between them, so it is
                # not passing through anything -- and a form here would place the data by a
                # number that means nothing. No form: the edge says nothing about this axis.
                continue
            forms[out_axis] = _identity_form(out_axis, step.input_axes, factor)

    return forms


def _params_matrix(params: dict, rank_in: int, rank_out: int) -> list[list[float]]:
    """The sub-matrix a BY_DIMENSION carries in its own parameters.

    Scale then translation, the order `to_matrix` already uses for a SEQUENCE, because a
    BY_DIMENSION is what `build_registration_edge` writes and
    ``_OPTIONAL_PARAMS_BY_KIND`` lets it carry both.
    """
    if "affine" in params:
        matrix = identity_matrix(max(rank_in, rank_out))
        for i, row in enumerate(params["affine"]):
            for j, value in enumerate(row):
                matrix[i][j] = float(value)
        return matrix

    matrix = identity_matrix(rank_in)
    if "scale" in params:
        matrix = matmul(to_matrix(enums.TransformKindChoices.SCALE.value, params, rank_in), matrix)
    if "translation" in params:
        matrix = matmul(to_matrix(enums.TransformKindChoices.TRANSLATION.value, params, rank_in), matrix)
    return matrix


def step_forms(step: AxedStep) -> dict[str, AxedForm]:
    """One affine functional per output axis this edge constrains, over its input axes.

    An output axis is absent exactly when the edge says nothing about it. Raises
    :class:`NonAffineTransformError` for the kinds with no closed form at all -- a FIELD,
    whose map is an array, and an UNMAPPABLE, which denies the correspondence outright.
    """
    if step.kind == enums.TransformKindChoices.BY_DIMENSION.value:
        return _by_dimension_forms(step)

    if step.kind == enums.TransformKindChoices.IDENTITY.value and step.input_axes == step.output_axes:
        # An IDENTITY passes every axis through, so every axis of it is the pass-through case
        # `_pass_through_factor` exists for. Without units on either side each factor is 1.0
        # and this is exactly what `_step_matrix` produced. Guarded on the two orders being
        # equal so that a hand-built step whose endpoints disagree -- which
        # `assert_edge_rank` forbids for a stored IDENTITY, ordered equality being its one
        # rule -- keeps its old answer rather than acquiring a new one here.
        forms: dict[str, AxedForm] = {}
        for axis in step.output_axes:
            factor = _pass_through_factor(step, axis)
            if factor is not None:
                forms[axis] = _identity_form(axis, step.input_axes, factor)
        return forms

    rank = len(step.input_axes)
    if step.kind == enums.TransformKindChoices.MAP_AXIS.value:
        # `permutation_matrix` writes its rows in the *input* system's axis order, because a
        # permutation relabels rather than reshapes -- so the row labels are the input's.
        matrix = permutation_matrix(list(step.acts_on_input or ()), list(step.acts_on_output or ()), list(step.input_axes))
        forms = _forms_from_matrix(matrix, step.input_axes, rank)
        return {axis: form for axis, form in forms.items() if axis in step.output_axes}

    if step.kind in _WHOLE_MATRIX_KINDS and "affine" in step.params:
        # **The rows already are the functionals.** An `affine` is M x (N+1) -- one row per
        # *output* axis, one column per input axis, plus the translation -- so it states its
        # own output rank and needs no square matrix in between. Which matters, because
        # `assert_edge_rank` admits a rank-changing AFFINE deliberately ("M x (N+1) and
        # rectangular *by design*"): routing it through `to_matrix`, which builds one square
        # matrix at the *input* rank, either drops rows or runs off the end of it.
        return _forms_from_matrix(step.params["affine"], step.output_axes, rank)

    if len(step.output_axes) != rank:
        # What is left here carries one number per input axis (or none at all), so the map it
        # describes is square and cannot reach a different output rank. `assert_edge_rank`
        # checks a scale or translation against the *input* rank only, so such an edge is
        # writable and only shows up here.
        raise NonAffineTransformError(f"a {step.kind} edge maps {rank} axes onto {len(step.output_axes)}, so its parameters are not a square map -- only BY_DIMENSION states a rank change")

    return _forms_from_matrix(_step_matrix(step, rank), step.output_axes, rank)


#: The kinds whose parameters are a whole matrix rather than one number per axis, and which
#: therefore state their own output rank in their row count. Beside `_PER_AXIS_KINDS` in
#: `core.logic.graph`, which draws the same line from the other side.
_WHOLE_MATRIX_KINDS = frozenset(
    {
        enums.TransformKindChoices.AFFINE.value,
        enums.TransformKindChoices.ROTATION.value,
    }
)


def _step_matrix(step: AxedStep, rank: int) -> list[list[float]]:
    """The homogeneous matrix of a step whose axes do not change: its steps' composed, or its own.

    **A SEQUENCE keeps its map on its steps, and `to_matrix` only ever sees params.** One written
    by `graph._sequence` carries `params={}` with the scale on step 0 and the translation on
    step 1, so calling `to_matrix(SEQUENCE, {}, rank)` returns the *identity* -- silently,
    because that branch simply finds neither key. Every stepped lens (`lens_to_parent`) and
    every pyramid level with a half-voxel offset (`create_level_edge`) is such an edge, so the
    steps are composed here, and `graph._edge_params` flattens them into one params dict for
    the callers that go through `to_matrix`.
    """
    if step.steps:
        return compose(list(step.steps), rank)
    return to_matrix(step.kind, step.params, rank)


def compose_forms(steps: Sequence[AxedStep], source_axes: Sequence[str]) -> dict[str, AxedForm]:
    """Compose a path into one functional per destination axis it constrains.

    **Substitution, not per-step boxing.** Re-bounding a box at every step inflates it under
    any rotation -- the very error `transformed_bbox` transforms all the corners to avoid --
    and an over-approximation here is a claim nobody asked for. Composing the functionals
    symbolically and bounding once at the end is exact.

    A destination axis whose functional would need a source axis some earlier step stopped
    constraining is dropped: once the path has let go of an axis, nothing downstream can
    take hold of it again.
    """
    rank = len(source_axes)
    current: dict[str, AxedForm] = {axis: _identity_form(axis, source_axes) for axis in source_axes}

    for step in steps:
        produced = step_forms(step)
        composed: dict[str, AxedForm] = {}
        for out_axis, form in produced.items():
            coefficients = [0.0] * rank
            constant = form.constant
            unconstrained = False
            for index, factor in enumerate(form.coefficients):
                if factor == 0.0:
                    continue
                inner = current.get(step.input_axes[index]) if index < len(step.input_axes) else None
                if inner is None:
                    unconstrained = True
                    break
                for position, value in enumerate(inner.coefficients):
                    coefficients[position] += factor * value
                constant += factor * inner.constant
            if unconstrained:
                continue
            composed[out_axis] = AxedForm(coefficients=tuple(coefficients), constant=constant)
        current = composed

    return current


def _sub_matrix(step: AxedStep) -> list[list[float]]:
    """The square map a BY_DIMENSION carries over the axes it names, as a homogeneous matrix.

    The same expression `_by_dimension_forms` builds its `sub` from, factored out so the
    inverse below cannot compose the forward map differently from the way it is read.
    """
    rank = len(step.acts_on_input or ())
    # The map is the params and nothing else: an `affine`, or a `scale` and a `translation`,
    # or none of them, which is the identity on the named axes (`_params_matrix`).
    return _params_matrix(step.params, rank, len(step.acts_on_output or ()))



def _matrix_step(step: AxedStep, matrix: list[list[float]], *, kind: str | None = None) -> AxedStep:
    """A reversed step carrying one homogeneous matrix as its `affine`, endpoints swapped.

    The matrix is homogeneous and (n+1) x (n+1); an `affine` parameter is M x (N+1) -- one
    row per output axis -- so the trailing `[0, ..., 0, 1]` row goes, being the homogeneous
    bookkeeping rather than a coordinate.
    """
    return AxedStep(
        kind=kind or step.kind,
        params={"affine": [list(row) for row in matrix[:-1]]},
        input_axes=step.output_axes,
        output_axes=step.input_axes,
        acts_on_input=step.acts_on_output,
        acts_on_output=step.acts_on_input,
        steps=(),
    )


def invert_step(step: AxedStep) -> AxedStep:
    """A step walked against its stored direction, as a forward step.

    A placement path hands back ``(edge, inverted)`` pairs and leaves the undoing to whoever
    composes them; this is that undoing, once, so no two composers disagree about it.

    **Every step reaching here is square.** `graph.adjacency_of` offers an edge backwards
    only where `is_reverse_traversable` holds, and that requires equal rank on the two sides
    as well as an invertible kind -- so a rank-changing edge is never walked backwards and
    the general solver below is only ever asked a well-posed question.

    Per kind, so :func:`invert_matrix` is the last resort rather than the first: negating a
    translation is exact, and solving a matrix for it would introduce rounding into a step
    that had none. The two kinds with no inverse at any rank -- FIELD and UNMAPPABLE -- are
    unreachable (`is_invertible` excludes them, so the walk never flags one) and raise rather
    than fall through to a wrong answer if that ever stops being true.
    """
    kind = step.kind
    swapped = {
        "input_axes": step.output_axes,
        "output_axes": step.input_axes,
        # The units swap with the axes they belong to, or an inverted step would convert the
        # wrong way -- and a pass-through would come back scaled by the factor instead of by
        # its reciprocal.
        "input_units": step.output_units,
        "output_units": step.input_units,
    }

    if kind == enums.TransformKindChoices.IDENTITY.value:
        return AxedStep(kind=kind, params={}, **swapped)

    if kind == enums.TransformKindChoices.TRANSLATION.value:
        return AxedStep(kind=kind, params={"translation": [-float(value) for value in step.params.get("translation") or ()]}, **swapped)

    if kind == enums.TransformKindChoices.SCALE.value:
        factors = [float(value) for value in step.params.get("scale") or ()]
        # `assert_no_collapsed_factors` forbids a zero factor at both write altitudes, so
        # this is unreachable through any current path -- and rows predating that rule are
        # exactly the ones nobody would think to check, so it is checked.
        collapsed = [index for index, factor in enumerate(factors) if factor == 0.0]
        if collapsed:
            raise SingularTransformError(f"A SCALE map with a zero factor at {collapsed} ({factors}) collapses that axis onto a point, so it cannot be walked backwards")
        return AxedStep(kind=kind, params={"scale": [1.0 / factor for factor in factors]}, **swapped)

    if kind == enums.TransformKindChoices.MAP_AXIS.value:
        # A permutation's inverse is the permutation read the other way round, and no
        # arithmetic at all. Sound because the two endpoint systems carry the same set of
        # axis names -- `graph.assert_edge_rank` holds a MAP_AXIS to exactly that, which is
        # also what makes `permutation_matrix` total.
        return AxedStep(kind=kind, params={}, acts_on_input=step.acts_on_output, acts_on_output=step.acts_on_input, **swapped)

    if kind == enums.TransformKindChoices.BY_DIMENSION.value:
        # Square over the *named subset*, which `assert_edge_rank` maps one for one -- not
        # over the endpoints, which a BY_DIMENSION is free to relate across a rank change.
        # The axes it does not name pass through, and passing through inverts to itself.
        return _matrix_step(step, invert_matrix(_sub_matrix(step)))

    if kind in (enums.TransformKindChoices.AFFINE.value, enums.TransformKindChoices.ROTATION.value, enums.TransformKindChoices.SEQUENCE.value):
        rank = len(step.input_axes)
        # AFFINE, so the result reads as what it is. The inverse of a rotation is a rotation
        # and the inverse of a scale-then-translate sequence is neither -- keeping either
        # label would make `invariance_of`-shaped reasoning over the returned step wrong.
        return _matrix_step(step, invert_matrix(_step_matrix(step, rank)), kind=enums.TransformKindChoices.AFFINE.value)

    raise NonAffineTransformError(f"A {kind} edge has no inverse at any rank, so a path may not walk it backwards")


def forms_to_matrix(forms: dict[str, AxedForm], destination_axes: Sequence[str]) -> tuple[list[list[float]], list[str]]:
    """Composed functionals as one M x (N+1) matrix, and the axes its rows are over.

    Rows in the **destination system's own axis order**, and only for the axes the path
    actually constrains: an axis with no form gets no row, exactly as `AxisExtent` gives an
    unconstrained axis no entry. Writing a zero row instead would pin the data at that
    axis' origin, which is a claim nobody made -- the same reason `_by_dimension_forms`
    leaves an axis out rather than zeroing it.

    Columns are the source axis order the forms were composed over, and the last column is
    the translation: the layout `AffineTransformation.affine` already uses.
    """
    rows = [axis for axis in destination_axes if axis in forms]
    return [[*(float(value) for value in forms[axis].coefficients), float(forms[axis].constant)] for axis in rows], rows


def form_interval(form: AxedForm, mins: Sequence[float], maxs: Sequence[float]) -> list[float]:
    """The exact range of one affine functional over an axis-aligned box.

    The closed form of what :func:`transformed_bbox` does by enumerating corners: an affine
    functional attains its extremes at a corner, and the sign of each coefficient decides
    which bound that term takes, independently of every other term. O(n) rather than
    O(2**n), which matters because every anchor of every source pays it -- and pinned equal
    to the corner enumeration by test, since "obviously equivalent" is how a half-voxel goes
    missing.
    """
    low = high = form.constant
    for index, factor in enumerate(form.coefficients):
        if factor == 0.0:
            continue
        a, b = factor * mins[index], factor * maxs[index]
        low += min(a, b)
        high += max(a, b)
    return [low, high]


def axed_bbox(mins: Sequence[float], maxs: Sequence[float], forms: dict[str, AxedForm]) -> dict[str, list[float]]:
    """The axis-keyed extent of a box under an already-composed path."""
    return {axis: form_interval(form, mins, maxs) for axis, form in forms.items()}


def annotation_box(axis_names: Sequence[str], vectors: Sequence[Sequence[float]], pins: Mapping[str, float], *, padded: bool = True) -> dict[str, list[float]] | None:
    """An annotation's axis-keyed box in its drawing space: its vertices on the axes they span, its pins on the axes they name.

    A shape may have fewer components than its drawing space has axes -- a rectangle on one
    plane of a volume -- and its pins are how it says which plane. So its vertices span the
    axes it does not pin, or the trailing ones where the pins do not account for the
    difference. An axis neither names is one the shape says nothing about, and it is left out
    of the box rather than handed a zero that would put the shape on the first plane.

    ``padded`` grows the spanned box by half a voxel either side (:func:`vectors_bbox`), the
    form the overlap filter compares voxel-aligned extents against; unpadded (:func:`aabb`) is
    the shape's own reach, the form a value window is tested against. A pin is a centre
    either way. None for a shape with no vertices, or more components than there are axes.
    """
    if not vectors:
        return None
    low, high = vectors_bbox(vectors) if padded else aabb(vectors)
    if len(low) > len(axis_names):
        return None
    unpinned = [name for name in axis_names if name not in pins]
    spanned = unpinned if len(unpinned) == len(low) else list(axis_names[len(axis_names) - len(low) :])
    box: dict[str, list[float]] = {}
    for name in axis_names:
        if name in spanned:
            index = spanned.index(name)
            box[name] = [low[index], high[index]]
        elif name in pins:
            box[name] = [float(pins[name]), float(pins[name])]
    return box


def window_bounds(windows: Iterable["WindowLike"]) -> dict[str, list[float]]:
    """A lens' windows as an axis-keyed box: an open side is unbounded, an un-windowed axis absent.

    Absence, not infinity, is how an axis says nothing in a box: :func:`boxes_overlap` is a
    conjunction over the axes both sides name. The infinities here stay inside the comparison;
    nothing serializes them.
    """
    return {window.axis: [-math.inf if window.min is None else float(window.min), math.inf if window.max is None else float(window.max)] for window in windows}


def window_box(axis_names: Sequence[str], windows: Iterable["WindowLike"]) -> tuple[list[float], list[float]]:
    """A windowed lens' box, positional over its container's axes: a window's bounds on the axes it names, infinite elsewhere.

    The infinities are internal: they ride through :func:`form_interval` (a zero coefficient
    is skipped, so no ``0 * inf``) and come out as infinite intervals, which
    :func:`bounded_axes` drops before anything is serialized or compared. A box says nothing
    about an axis by leaving it out, never by writing a number for it.
    """
    bounds = window_bounds(windows)
    mins = [bounds[axis][0] if axis in bounds else -math.inf for axis in axis_names]
    maxs = [bounds[axis][1] if axis in bounds else math.inf for axis in axis_names]
    return mins, maxs


def bounded_axes(box: dict[str, list[float]]) -> dict[str, list[float]]:
    """The axes of a box that are actually bounded: an interval with an infinite end is dropped.

    The one place an infinity is retired. A box that leaves an axis out says nothing about it,
    which is what :func:`boxes_overlap` reads; an infinity written for it would survive into a
    GraphQL Float, which cannot carry one.
    """
    return {axis: interval for axis, interval in box.items() if math.isfinite(interval[0]) and math.isfinite(interval[1])}


def shares_a_bounded_axis(a: dict[str, list[float]], b: dict[str, list[float]]) -> bool:
    """Whether two boxes constrain any axis in common -- without one, their overlap is vacuous and has not been shown."""
    return bool(set(a) & set(b))


def compose_slices(dataset_shape: Sequence[int], dataset_axis_names: Sequence[str], parent_slices: Iterable["SliceLike"], child_slices: Iterable["SliceLike"]) -> list[dict[str, int | str]]:
    """A crop of a crop, as slices of the dataset: the child's slices, stated in the parent lens' own index space, composed through the parent's.

    Lens voxel ``i`` of the parent is dataset voxel ``ps + i·pstep``, so a child slice
    ``cs:ce:cst`` over the parent keeps dataset voxels ``ps + pstep·(cs + k·cst)`` -- a slice
    with start ``ps + pstep·cs``, step ``pstep·cst`` and as many voxels as the child keeps. The
    child's spellings resolve against the parent's extent by Python's own rules (negatives,
    open bounds, a stop past the end), and the result is handed to :func:`normalize_slices`,
    so a crop of a crop that equals an existing crop *is* that crop. An axis the child does
    not name keeps the parent's selection; a child slice that keeps nothing is refused.
    """
    parent_by_axis = {slice_.axis: slice_ for slice_ in parent_slices}
    child_by_axis: dict[str, SliceLike] = {}
    for slice_ in child_slices:
        if slice_.axis not in dataset_axis_names:
            raise ValueError(f"Axis '{slice_.axis}' is not an axis of the dataset, which the lens does not have either; its axes are {list(dataset_axis_names)}")
        if slice_.axis in child_by_axis:
            raise ValueError(f"Axis '{slice_.axis}' is sliced twice; one slice per axis")
        child_by_axis[slice_.axis] = slice_
    composed: list[dict[str, int | str]] = []
    for axis, size in zip(dataset_axis_names, dataset_shape, strict=True):
        parent = parent_by_axis.get(axis)
        child = child_by_axis.get(axis)
        if parent is None and child is None:
            continue
        if parent is not None:
            ps, pe, pstep = slice(parent.start, parent.stop, parent.step).indices(size)
        else:
            ps, pe, pstep = 0, size, 1
        extent = len(range(ps, pe, pstep))
        if child is None:
            composed.append({"axis": axis, "start": ps, "stop": pe, "step": pstep})
            continue
        if child.step is not None and child.step <= 0:
            raise ValueError(f"Slice over axis '{axis}' has step {child.step}; a lens keeps every k-th voxel for a positive k")
        cs, ce, cst = slice(child.start, child.stop, child.step).indices(extent)
        count = len(range(cs, ce, cst))
        if count == 0:
            raise ValueError(f"Slice over axis '{axis}' ({child.start}:{child.stop}:{child.step}) selects nothing of the {extent} voxels the lens has along it")
        start = ps + pstep * cs
        step = pstep * cst
        composed.append({"axis": axis, "start": start, "stop": start + (count - 1) * step + 1, "step": step})
    return composed


def lens_within(dataset_shape: Sequence[int], dataset_axis_names: Sequence[str], parent_slices: Iterable["SliceLike"], child_slices: Iterable["SliceLike"]) -> bool:
    """Whether every voxel a child lens keeps is one its parent keeps: a crop of a crop, however spelled.

    Per axis, the child's kept indices are a subset of the parent's. Decided on the index sets,
    so a stepped parent and a child that lands between its steps is refused, and a child that
    spells the parent's own selection differently is accepted.
    """
    parent_by_axis = {slice_.axis: slice_ for slice_ in parent_slices}
    child_by_axis = {slice_.axis: slice_ for slice_ in child_slices}
    for axis, size in zip(dataset_axis_names, dataset_shape, strict=True):
        parent = parent_by_axis.get(axis)
        child = child_by_axis.get(axis)
        if child is None and parent is None:
            continue
        kept_by_parent = set(range(*slice(parent.start, parent.stop, parent.step).indices(size))) if parent is not None else set(range(size))
        kept_by_child = set(range(*slice(child.start, child.stop, child.step).indices(size))) if child is not None else set(range(size))
        if not kept_by_child <= kept_by_parent:
            return False
    return True


def windows_within(parent_windows: Iterable["WindowLike"], child_windows: Iterable["WindowLike"]) -> bool:
    """Whether every row a child window keeps is one its parent keeps: per axis the child's bounds lie inside the parent's, and an axis the parent bounds the child bounds too."""
    child_by_axis = {window.axis: window for window in child_windows}
    for parent in parent_windows:
        child = child_by_axis.get(parent.axis)
        if child is None:
            return False
        if parent.min is not None and (child.min is None or child.min < parent.min):
            return False
        if parent.max is not None and (child.max is None or child.max > parent.max):
            return False
    return True


def channel_extents(axis_specs: Sequence[AxisSpec], shape: Sequence[int]) -> dict[str, int]:
    """The extent of each CHANNEL axis of a lens, by name: what decides whether the lens pins one channel."""
    return {spec.name: extent for spec, extent in zip(axis_specs, shape, strict=True) if spec.type == enums.AxisTypeChoices.CHANNEL.value}


def intersect_windows(parent_windows: Iterable["WindowLike"], child_windows: Iterable["WindowLike"]) -> list[dict[str, float | str | None]]:
    """A window of a window: per axis the tighter of the two bounds, an open side staying open, an axis only one names passing through.

    Handed to :func:`normalize_windows`, which refuses a window that keeps nothing (a min at
    or above its max), so a selection inside a selection is one row like any other.
    """
    bounds: dict[str, list[float | None]] = {}
    named: set[str] = set()
    for window in child_windows:
        if window.axis in named:
            raise ValueError(f"Axis '{window.axis}' is windowed twice; one window per axis")
        named.add(window.axis)
    for window in (*parent_windows, *child_windows):
        low, high = bounds.setdefault(window.axis, [None, None])
        if window.min is not None:
            low = float(window.min) if low is None else max(low, float(window.min))
        if window.max is not None:
            high = float(window.max) if high is None else min(high, float(window.max))
        bounds[window.axis] = [low, high]
    return [{"axis": axis, "min": low, "max": high} for axis, (low, high) in bounds.items()]


def box_to_slices(
    forms: dict[str, AxedForm],
    source_axis_names: Sequence[str],
    mins: Sequence[float],
    maxs: Sequence[float],
    dataset_axis_names: Sequence[str],
    dataset_shape: Sequence[int],
    *,
    pins: Mapping[str, int] | None = None,
) -> list[dict[str, int | str]]:
    """The slices of a dataset that a box stated in another space covers, once that space is composed into the dataset's grid.

    ``forms`` is the composed map from the box's space into the grid, one functional per
    dataset axis it reaches (:func:`compose_forms`), its coefficients over ``source_axis_names``;
    ``mins``/``maxs`` the box, positional over those same axes, infinite where open. An output axis the map does not reach is left whole,
    unless ``pins`` names it (a pinned index keeps that one voxel); so is one the box leaves
    open, and a side left open runs to the array's edge. A dataset voxel ``n`` covers
    ``[n - 0.5, n + 0.5)``, and it is kept when its cell meets the box: ``n > lo - 0.5`` and
    ``n < hi + 0.5``, which is the half-open rule the overlap filter applies, so a box ending
    exactly on a voxel face does not take the voxel beyond it. Clamped to the array; a box
    that lands outside it is refused.
    """
    pinned = dict(pins or {})
    slices: list[dict[str, int | str]] = []
    for axis, size in zip(dataset_axis_names, dataset_shape, strict=True):
        form = forms.get(axis)
        if form is None:
            if axis in pinned:
                value = int(pinned[axis])
                if not 0 <= value < size:
                    raise ValueError(f"Pin {axis}={value} lies outside the dataset, whose axis '{axis}' has {size} positions")
                slices.append({"axis": axis, "start": value, "stop": value + 1, "step": 1})
            continue
        low, high = form_interval(form, mins, maxs)
        start = max(0, math.floor(low - 0.5) + 1) if math.isfinite(low) else 0
        stop = min(size, math.ceil(high + 0.5)) if math.isfinite(high) else size
        if start >= stop:
            raise ValueError(f"The box lies outside the dataset along '{axis}': it covers [{low}, {high}] and the axis spans [-0.5, {size - 0.5}]")
        slices.append({"axis": axis, "start": start, "stop": stop, "step": 1})
    return slices


def boxes_overlap(a: dict[str, list[float]], b: dict[str, list[float]], *, closed: bool = True) -> bool:
    """Whether two axis-keyed boxes overlap: a conjunction over the axes they BOTH constrain.

    An axis only one side constrains cannot exclude anything. A source registered on (y, x)
    alone really is in every z slice of the world, and a region naming only the first two
    axes really does say nothing about the rest -- so in both directions, silence is not a
    zero to be tested against.

    Bounds are inclusive by default, on purpose. A degenerate box -- ``min == max``, the plane
    probe a client sends to ask what is under this slice -- must still meet the sources
    containing it, and a strict comparison answers no to every one of them.

    ``closed=False`` is for two boxes that are both made of voxels: a crop's extent and the
    box around a drawn shape are each ``[first - 0.5, last + 0.5]``, so a crop starting at row
    21 and a shape ending on row 20 share the face at 20.5 and no voxel. Sharing a face is not
    overlapping, and the inclusive test would say it is -- an off-by-one at every crop
    boundary. A degenerate probe at an integer position still meets the voxel box around it
    under this form, since a voxel's faces sit at halves and never on the probe.
    """
    for axis, (low, high) in a.items():
        other = b.get(axis)
        if other is None:
            continue
        if not closed:
            if not (low < other[1] and other[0] < high):
                return False
            continue
        if high < other[0] or other[1] < low:
            return False
    return True
