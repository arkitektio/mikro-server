"""Building and querying the coordinate graph.

The ORM-touching half of the coordinate work; the pure arithmetic lives in
:mod:`core.logic.coords`. Every write path that creates a coordinate system, an
axis or an edge goes through here, so that the derivations happen exactly once
and their results are what get stored.

Nothing here *stores* a composed path to world, and that is the rule: the same dataset
can sit in two scenes under two different registrations, so any single stored answer
would be wrong in one of them. Composing on read is fine and happens here --
:func:`condense_path` is what backs ``Layer.asAffine`` -- because a value derived per
request cannot go stale. See :mod:`core.models.coords`.
"""

import dataclasses
import heapq
from typing import TYPE_CHECKING, Iterable

from django.db.models import F, Q

from kanne_server import scalars as kanne_scalars

from core import enums, models
from core.creation import CreationContext
from core.inputs.coords import assert_no_collapsed_factors, assert_no_collapsed_rows, assert_nonsingular_matrix
from core.logic import coords as coords_logic

if TYPE_CHECKING:
    from authentikate.models import Organization


@dataclasses.dataclass(frozen=True)
class Container:
    """One kind of thing that can live in a coordinate system.

    Six of them, and the list was hand-written in six places with six different shapes --
    the ``residents`` field and its prefetch, ``placedSystems``' prefetch,
    ``residents_exist``, ``_assert_shared`` and ``SpaceGraph``. Every one of them had to be
    found and edited together, and nothing said so; this is that list, once.
    """

    #: The container model.
    model: type
    #: The reverse accessor on ``CoordinateSystem`` -- also the ``prefetch_related`` name.
    related_name: str
    #: The field on the *container* holding the id of whatever tree it belongs to. For a
    #: dataset's own parts that is the dataset; a collection is its own root, so ``pk``.
    root_field: str
    #: The first half of a container key, and **not** derivable from the model: a dataset,
    #: its levels and its lenses are three models sharing one key, because they are one node
    #: of the fact tree. Written here once so the map that builds a key and the lookups that
    #: turn one back into rows cannot disagree -- reading it off ``model.__name__`` instead
    #: made ``ArrayDataset`` key as ``"dataset"`` going in and be looked up as ``"array_dataset"``
    #: coming out, which dropped every dataset from the answer without an error.
    key: str
    #: Whether this container is a collection -- a thing that owns its space outright,
    #: rather than a part of a dataset that shares the dataset's tree.
    is_collection: bool = False


#: **Presentation order**: the outermost thing first, so a dataset's own space lists the
#: dataset before its level and its lens. This is the order `CoordinateSystem.residents`
#: returns and `test_scene_over_owned_system` asserts, which is why it is stated here rather
#: than left to whatever order a resolver happened to concatenate in.
CONTAINERS: tuple[Container, ...] = (
    Container(model=models.ArrayDataset, related_name="datasets", root_field="pk", key="dataset"),
    Container(model=models.DataArray, related_name="data_arrays", root_field="dataset_id", key="dataset"),
    Container(model=models.Lens, related_name="lenses", root_field="dataset_id", key="dataset"),
    Container(model=models.MeshCollection, related_name="mesh_collections", root_field="pk", key="meshcollection", is_collection=True),
    Container(model=models.NetworkCollection, related_name="network_collections", root_field="pk", key="networkcollection", is_collection=True),
    Container(model=models.TableDataset, related_name="table_datasets", root_field="pk", key="tabledataset", is_collection=True),
    Container(model=models.AnnotationCollection, related_name="annotation_collections", root_field="pk", key="annotationcollection", is_collection=True),
    # The seventh, and it was missing rather than excluded. `SparseDataset.coordinate_system`
    # has always existed and is read by `identified_axes` and by the attribute-plan walk -- but
    # the model was in none of the six hand-written lists this class replaced, so its space was
    # simultaneously "somewhere data lives" to those readers and, because `_UNINHABITED` did not
    # know to ask about it, an *uninhabited reference frame* to the walk. A sparse-only space
    # therefore keyed as a world and was excluded from the `fact_paths` frontier on both sides.
    # Those two readings cannot both be right; this is the one that matches the FK.
    Container(model=models.SparseDataset, related_name="sparse_datasets", root_field="pk", key="sparsedataset", is_collection=True),
)

#: The model a container key resolves back to. A key names one *node*, so the three models
#: sharing the ``dataset`` key resolve to the one that is the node: the dataset itself.
MODEL_BY_KEY: dict[str, type] = {container.key: container.model for container in reversed(CONTAINERS)}

#: **Keying order**, which is the reverse question and deliberately not the same tuple: a
#: space several containers live in must key to the *dataset* when the dataset itself lives
#: there, so the dataset is written last and overwrites its own parts. Two orders, both
#: load-bearing, neither derivable from the other.
_KEYING_ORDER: tuple[Container, ...] = tuple(sorted(CONTAINERS, key=lambda container: container.model is models.ArrayDataset))

#: The reverse accessors, for ``prefetch_related`` and for the "does anything live here"
#: fan-out. Derived, so a seventh container is one line above and nothing else.
RESIDENT_RELATIONS: tuple[str, ...] = tuple(container.related_name for container in CONTAINERS)

#: The collections alone -- the containers that own their space rather than sharing a
#: dataset's. :func:`collection_in` and the seeding paths ask for exactly these.
COLLECTION_CONTAINERS: tuple[Container, ...] = tuple(container for container in CONTAINERS if container.is_collection)


def create_pixel_axes(system: "models.CoordinateSystem", axes: list) -> list["models.Axis"]:
    """Write a pixel-space system's axes, enumerating them so `order` is the array index.

    ``Axis.order`` being the array-dimension index is load-bearing: it is what ties
    ``scale[i]`` to ``shape[i]``, and what makes "the last spatial axis is x" a
    well-defined statement. It is always written by enumeration, never supplied by
    a caller.

    Pixel axes (INTRINSIC and ARRAY systems) keep their names and semantic types
    -- a z axis is spatial whether it holds indices or micrometres, and the render
    axes are derived from the types -- but they never carry a unit. Units belong
    to unit-carrying systems -- physical spaces and worlds -- only.
    """
    axis_specs = [coords_logic.AxisSpec(name=axis.name, type=axis.type.value if hasattr(axis.type, "value") else axis.type) for axis in axes]
    coords_logic.assert_axis_names_unique(axis_specs)
    coords_logic.assert_at_most_one_time_axis(axis_specs)

    rows = []
    for index, axis in enumerate(axes):
        axis_type = axis.type.value if hasattr(axis.type, "value") else axis.type
        rows.append(
            models.Axis(
                coordinate_system=system,
                order=index,
                name=axis.name,
                type=axis_type,
                unit=None,
                long_name=axis.long_name,
                description=axis.description,
            )
        )
    created = models.Axis.objects.bulk_create(rows)

    # Materialize the dataset's structural spec at the one moment its immutable axes are
    # written. `system.datasets` is non-empty only for a dataset's own pixel grid (a pyramid
    # level's ARRAY system writes the same axes but describes no dataset), so this fires
    # exactly once per dataset and never for a level. The column is the read
    # path for `ArrayDataset.spec`; `specs_for_axes` stays its single source of truth.
    #
    # Written without a historical record: this is part of creating the dataset, not an edit
    # to it. Only `name` and `description` are audited edits, and a provenance row here would
    # read as a post-creation change to something that is fixed at creation.
    dataset = next(iter(system.datasets.all()[:1]), None)
    if dataset is not None:
        dataset.stored_spec = [spec.value for spec in coords_logic.specs_for_axes(axis_specs)]
        dataset.save_without_historical_record(update_fields=["stored_spec"])

    return created


def create_physical_axes(system: "models.CoordinateSystem", axes: list) -> list["models.Axis"]:
    """Write a unit-carrying system's axes -- a dataset's physical space, a shared world -- with their units.

    Every axis must carry a unit: a unit-carrying space without units is a pixel
    space wearing a costume. A unit pint cannot parse is worthless -- it will fail
    at the moment someone tries to convert with it, long after the write and far
    from whoever made it -- so it is rejected here, holding a direct ORM write to
    the same standard as the GraphQL boundary. 'a.u.' is the escape hatch for
    genuinely arbitrary units.

    The unit must also *measure the right thing*: a TIME axis in micrometres is
    rejected here rather than left to compose silently into a matrix. And a system
    carries at most one time axis -- a space with two clocks has no meaning, it just
    renders one of them.

    The axes must obey the RFC-5 type ordering, like every other axis writer's do: the
    render-axis derivation reads x/y/z off the *position* of the spatial axes, so a
    scrambled declaration does not fail, it renders wrong.
    """
    if not axes:
        # A space with no axes is not a space. It composes into nothing, renders nothing,
        # and every edge into it fails the rank check -- so it is rejected at the door
        # rather than left to be discovered by whatever tries to use it.
        raise ValueError(f"Coordinate system '{system.name}' was given no axes. A coordinate space needs at least one axis.")

    specs = [coords_logic.AxisSpec(name=axis.name, type=axis.type.value if hasattr(axis.type, "value") else axis.type) for axis in axes]
    coords_logic.assert_axis_names_unique(specs)
    coords_logic.assert_at_most_one_time_axis(specs)

    rows = []
    for index, axis in enumerate(axes):
        axis_type = axis.type.value if hasattr(axis.type, "value") else axis.type
        if axis.unit is None:
            raise ValueError(f"Axis '{axis.name}' of unit-carrying system '{system.name}' has no unit. Use 'a.u.' for arbitrary units; a unitless axis belongs to a pixel system.")

        # Parseable first, then dimensionally right: a unit pint cannot read has no
        # dimension to check, and "furlongs_per_fortnight is not a valid unit" is the
        # more useful of the two errors to hand back.
        unit = kanne_scalars.parse_unit(axis.unit)
        coords_logic.assert_unit_matches_type(axis.name, axis_type, unit)

        rows.append(
            models.Axis(
                coordinate_system=system,
                order=index,
                name=axis.name,
                type=axis_type,
                unit=unit,
                long_name=axis.long_name,
                description=axis.description,
            )
        )
    return models.Axis.objects.bulk_create(rows)


@dataclasses.dataclass(frozen=True)
class _TableAxisSpec:
    """An axis-typed `ColumnInput`, read under the names the axis writer below uses."""

    name: str
    axis_type: object
    unit: object
    long_name: object
    description: object


def create_table_axes(system: "models.CoordinateSystem", axes: list) -> list["models.Axis"]:
    """Write a table dataset's system axes from its declared axes, in the order given.

    Neither pixel nor calibrated: a table's coordinate columns carry a unit exactly
    when the client declared one -- pixel-index centroids do not, an SMLM
    localization in nanometres does -- so this is the one axis writer that treats the
    unit as optional-but-validated. It is all-or-nothing across the spatial axes: a
    half-calibrated space (one axis in nm, its sibling unitless) composes wrongly
    into a single matrix, so it is rejected rather than stored.

    **A table's axes are held to no type ordering** -- and neither is anything else any
    more. A parquet column's position is whatever the frame happened to have, and holding
    a table to an ordering meant refusing ``centroid_x, centroid_y, object_id`` -- a
    natural column order -- for nothing.

    For nothing quite literally, measured against this module's own logic: ``x, y, t`` was
    refused and ``t, x, y`` accepted, and *both* derive ``x=y, y=x`` -- identically,
    because :func:`resolve_render_axes` finds the time axis by a type scan and the spatial
    ones through ``spatial_axes()``, so where a TIME or INDEX axis sits among them changes
    nothing it computes. The rule refused what rendered no worse than what it accepted,
    which is why it is now gone for arrays too: the orderings it turned away there --
    ``(z, c, y, x)``, ``(c, z, y, x)`` -- are how acquisitions are ordinarily written.

    What the derivation *does* read is the relative order of the **spatial** axes --
    the last is x, the one before it y, the one before that z -- and that survives
    untouched here, because the columns are stored in the order they were given. It
    is also still unguarded: ``x, y, z`` derives ``x=z, z=x``, fully transposed, with
    no error. That is a real hole and a separate fix; see item 14 of the proposals
    doc. It was never caught by the ordering rule either.

    ``order`` is written by enumeration -- for a table it is the axis' position among the
    axis-typed columns, there being no array shape to index. Since the flat declaration,
    that IS the file's column order restricted to the axes: there is no separate list to
    reorder them with, because nothing strides a table by position -- consumers address
    axes by name, and an edge states its own axis lists where order matters.
    """
    # A `ColumnInput` with a non-null `axisType` names the column and the axis in one entry,
    # so the two vocabularies meet here and nowhere else. The caller passes the axis-typed
    # columns in declaration (= file) order, which IS the axis order: a table has no byte
    # order, its axes are named columns, and an edge wanting a different order states its own
    # axis lists.
    coordinate_columns = [
        _TableAxisSpec(name=column.name, axis_type=column.axis_type, unit=column.unit, long_name=column.long_name, description=column.description)
        for column in axes
    ]
    specs = [coords_logic.AxisSpec(name=col.name, type=col.axis_type.value if hasattr(col.axis_type, "value") else col.axis_type) for col in coordinate_columns]
    coords_logic.assert_axis_names_unique(specs)
    coords_logic.assert_at_most_one_time_axis(specs)

    spatial_units = [col.unit for col in coordinate_columns if (col.axis_type.value if hasattr(col.axis_type, "value") else col.axis_type) == enums.AxisTypeChoices.SPACE.value]
    if spatial_units and any(u is None for u in spatial_units) and any(u is not None for u in spatial_units):
        raise ValueError("A table's spatial coordinate columns must be all calibrated (each with a unit) or all pixel-index (none with a unit). A half-calibrated space composes wrongly into one matrix.")

    rows = []
    for index, col in enumerate(coordinate_columns):
        axis_type = col.axis_type.value if hasattr(col.axis_type, "value") else col.axis_type
        unit = None
        if col.unit is not None:
            unit = kanne_scalars.parse_unit(col.unit)
            coords_logic.assert_unit_matches_type(col.name, axis_type, unit)
        rows.append(
            models.Axis(
                coordinate_system=system,
                order=index,
                name=col.name,
                type=axis_type,
                unit=unit,
                long_name=col.long_name,
                description=col.description,
            )
        )
    return models.Axis.objects.bulk_create(rows)


def _sequence(
    *,
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
    scale: list[float],
    translation: list[float],
    ctx: CreationContext,
    validity: str | None = None,
) -> "models.Transformation":
    """A SEQUENCE edge of a scale then a translation, with the children RFC-5 permits to omit their endpoints."""
    sequence = models.Transformation.objects.create(
        kind=enums.TransformKindChoices.SEQUENCE.value,
        input=input_system,
        output=output_system,
        params={},
        validity=validity or enums.PlacementValidityChoices.VALIDATED.value,
        creator=ctx.user,
        organization=ctx.organization,
    )
    # The children omit input and output: the wrapping sequence supplies them.
    models.Transformation.objects.create(
        kind=enums.TransformKindChoices.SCALE.value,
        parent=sequence,
        order=0,
        params={"scale": scale},
        creator=ctx.user,
        organization=ctx.organization,
    )
    models.Transformation.objects.create(
        kind=enums.TransformKindChoices.TRANSLATION.value,
        parent=sequence,
        order=1,
        params={"translation": translation},
        creator=ctx.user,
        organization=ctx.organization,
    )
    return sequence


def create_level_edge(
    *,
    array_system: "models.CoordinateSystem",
    intrinsic: "models.CoordinateSystem",
    shape_0: list[int],
    shape_level: list[int],
    axis_specs: list[coords_logic.AxisSpec],
    ctx: CreationContext,
) -> "models.Transformation":
    """Store the edge placing one pyramid level in its dataset's intrinsic pixel space.

    The scale is **absolute** and dimensionless, derived from the actual shapes --
    so a pyramid whose axes do not halve cleanly (36 floors to 18, 9, 4, 2, 1,
    giving factors 1, 2, 4, **9, 18, 36**, not 1, 2, 4, 8, 16, 32) is described
    correctly rather than plausibly. The translation is the half-voxel offset the
    downsample introduces; without it every level above 0 draws offset from level 0.

    Derived once, here, at write time. If it were re-derived at read, every reader
    would have to agree on how -- and one of them would not.
    """
    scale, translation = coords_logic.pyramid_transform(shape_0, shape_level, axis_specs)

    if all(offset == 0 for offset in translation):
        # Level 0, or any level that happens not to be downsampled: a scale suffices.
        return models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SCALE.value,
            input=array_system,
            output=intrinsic,
            params={"scale": scale},
            creator=ctx.user,
            organization=ctx.organization,
        )

    return _sequence(input_system=array_system, output_system=intrinsic, scale=scale, translation=translation, ctx=ctx)


def create_lens_edge(
    *,
    lens_system: "models.CoordinateSystem",
    parent_system: "models.CoordinateSystem",
    dataset_axis_names: list[str],
    slices: list,
    ctx: CreationContext,
) -> "models.Transformation":
    """Store the edge placing a sliced lens back in its dataset's intrinsic pixel space.

    A pure crop is a translation of the slice starts. A *stepped* lens also
    rescales, so it is a sequence -- a translation-only edge would mis-place every
    subsampled lens, and would do it without complaining. An unsliced lens never
    gets here: it owns no system, because its space is the intrinsic space itself.
    """
    kind, params = coords_logic.lens_to_parent(dataset_axis_names, slices)

    if kind == enums.TransformKindChoices.TRANSLATION.value:
        return models.Transformation.objects.create(
            kind=kind,
            input=lens_system,
            output=parent_system,
            params=params,
            creator=ctx.user,
            organization=ctx.organization,
        )

    return _sequence(
        input_system=lens_system,
        output_system=parent_system,
        scale=params["scale"],
        translation=params["translation"],
        ctx=ctx,
    )


def edge_axis_names(edge: "models.Transformation", side: str) -> list[str]:
    """The axis names an edge's parameters are ordered by, on one side.

    ``scale``, ``translation`` and the columns of ``affine`` are written in the axis
    order of the edge's *input system* -- not in the order of whatever layer happens to
    be reading them. When the two differ (a [z,y,x] physical system under a [t,c,z,y,x]
    layer), a client that indexes the arrays against its own axis names puts the numbers on
    the wrong axes, and the result is plausible rather than obviously broken.

    So every edge states its own order, and a client never needs a side index of the
    scene's coordinate systems to recover it. A BY_DIMENSION edge names a *subset* --
    the axes it acts on, the ones it leaves alone being exactly those it does not name
    -- and that stored list is authoritative. Every other kind acts on all of them, in
    order.

    A SEQUENCE's children omit their own endpoints on purpose (the wrapper supplies
    them), so they inherit the wrapper's.
    """
    stored = edge.input_axes if side == "input" else edge.output_axes
    if stored:
        return list(stored)

    system = edge.input if side == "input" else edge.output
    if system is None and edge.parent_id:
        parent = edge.parent
        system = parent.input if side == "input" else parent.output

    return [axis.name for axis in system.axes.all()] if system else []


#: Kinds whose inverse a client can actually compute. A FIELD has no closed-form inverse, so
#: rank alone would wave it through. That refusal is not merely a limit: a FIELD is
#: many-to-one on purpose -- an object is a set of pixels, a track is a set of observations --
#: so walking one backwards would ask for a point where there is a set. UNMAPPABLE is not
#: walked in any direction.
#:
#: There used to be a BIJECTION here, invertible by construction because it *carried* its
#: inverse. It was removed with the kind (proposals item 15, D2): nothing wrote one, and an
#: inverse that cannot be derived is still expressible -- as a FIELD, whose values are the
#: map, in whichever direction the author needs it.
_INVERTIBLE_KINDS = frozenset(
    {
        enums.TransformKindChoices.IDENTITY.value,
        enums.TransformKindChoices.SCALE.value,
        enums.TransformKindChoices.TRANSLATION.value,
        enums.TransformKindChoices.MAP_AXIS.value,
        enums.TransformKindChoices.AFFINE.value,
        enums.TransformKindChoices.ROTATION.value,
    }
)

#: Kinds that are invertible exactly when every one of their children is.
_WRAPPER_KINDS = frozenset(
    {
        enums.TransformKindChoices.SEQUENCE.value,
        enums.TransformKindChoices.BY_DIMENSION.value,
    }
)


def is_traversable(edge: "models.Transformation") -> bool:
    """Whether a placement search may use this edge **at all**, in either direction.

    Exactly one kind says no. An UNMAPPABLE edge declares that no point of its input
    corresponds to a point of its output, so a path that crossed it would be composing a
    map across a stated non-correspondence -- and would come back looking like every
    other path, plausible and wrong. The edge is a real fact and stays in the graph:
    discovery still returns it (that is how a client learns *why* the data cannot be
    placed), and the lineage still reads it. It is only the walk that refuses it.
    """
    return edge.kind != enums.TransformKindChoices.UNMAPPABLE.value


def is_invertible(edge: "models.Transformation") -> bool:
    """Whether this edge's map can be undone.

    Kind, not rank. A displacement field maps N axes to N axes and has no closed-form
    inverse at all; rank alone would happily offer it for inversion and hand the client
    an `inverted: true` step it cannot honour.

    A wrapper is invertible exactly when all of its children are -- a SEQUENCE whose
    second step is a warp field is not invertible because its first step is a scale. That
    recursion is the reason this is a function and not a set membership test.

    Not caught here, and worth knowing: a **singular** square AFFINE (a projection written
    as a matrix) passes both the kind gate and the rank gate and still has no inverse. This
    stays a kind-only predicate -- numerics do not belong in a metadata answer -- so the
    number is read at the one other moment it can be: at *write* time, by
    :func:`~core.inputs.coords.assert_nonsingular_matrix`, which refuses such a matrix
    before any walk can be offered it. The gap is closed one altitude down, not here.
    """
    if edge.kind in _WRAPPER_KINDS:
        children = list(edge.children.all())
        return all(is_invertible(child) for child in children) if children else True
    return edge.kind in _INVERTIBLE_KINDS


#: The invariance classes ordered strongest to weakest. Ranked here rather than inside each
#: caller because the wrapper recursion and the per-path aggregate must agree about what
#: "weaker" means.
_INVARIANCE_RANK: dict[str, int] = {
    enums.TransformInvariance.NONE.value: 0,
    enums.TransformInvariance.DIFFEOMORPHIC.value: 1,
    enums.TransformInvariance.AFFINE.value: 2,
    enums.TransformInvariance.SIMILARITY.value: 3,
    enums.TransformInvariance.ISOMETRY.value: 4,
}

#: The kinds whose invariance the kind alone settles. SCALE is absent: it is the one kind
#: needing a parameter read. The composites are absent because they recurse.
_INVARIANCE_BY_KIND: dict[str, str] = {
    enums.TransformKindChoices.IDENTITY.value: enums.TransformInvariance.ISOMETRY.value,
    enums.TransformKindChoices.TRANSLATION.value: enums.TransformInvariance.ISOMETRY.value,
    enums.TransformKindChoices.ROTATION.value: enums.TransformInvariance.ISOMETRY.value,
    enums.TransformKindChoices.MAP_AXIS.value: enums.TransformInvariance.ISOMETRY.value,
    enums.TransformKindChoices.AFFINE.value: enums.TransformInvariance.AFFINE.value,
    enums.TransformKindChoices.FIELD.value: enums.TransformInvariance.DIFFEOMORPHIC.value,
    enums.TransformKindChoices.UNMAPPABLE.value: enums.TransformInvariance.NONE.value,
}

#: Kinds whose invariance is the weakest of their children's. Equal to `_WRAPPER_KINDS` now
#: that BIJECTION is gone; it used to be wider by exactly that kind, because invertibility is
#: a property a BIJECTION *had* by construction so `is_invertible` never looked inside one --
#: but a pair of warp fields does not become a rigid map by carrying its own inverse, so
#: invariance had to look. Kept as its own name rather than aliased: the two sets answer
#: different questions and a future kind may separate them again.
_COMPOSITE_KINDS = frozenset(
    {
        enums.TransformKindChoices.SEQUENCE.value,
        enums.TransformKindChoices.BY_DIMENSION.value,
    }
)

#: What each optional parameter of a childless composite does to the geometry. `scale` is
#: absent because its answer depends on its entries.
_PARAM_INVARIANCE: dict[str, str] = {
    "translation": enums.TransformInvariance.ISOMETRY.value,
    "affine": enums.TransformInvariance.AFFINE.value,
}

#: How known each claim is, ordered weakest to strongest. Beside `_INVARIANCE_RANK` and for
#: the same reason: two copies of an order are two chances to rank it differently.
_VALIDITY_RANK: dict[str, int] = {
    enums.PlacementValidityChoices.UNKNOWN.value: 0,
    enums.PlacementValidityChoices.INFERRED.value: 1,
    enums.PlacementValidityChoices.MANUAL.value: 2,
    enums.PlacementValidityChoices.VALIDATED.value: 3,
}


def weakest_invariance(invariances: Iterable[str]) -> str:
    """The class of a composition: the weakest of the classes composed.

    Exact as a *membership* statement, not tight. The groups nest, so composing an element of
    a weaker group with one of a stronger lands in the weaker group at every level -- there is
    no composition this understates. It may overstate: a scale by 2 then a scale by 1/2 is the
    identity and still reads SIMILARITY, which is the same conservatism as an AFFINE reading
    AFFINE without an SVD, and errs in the same safe direction.

    Empty is ISOMETRY: the identity element of the order, and the honest answer for a
    placement path with no steps -- a space is isometric to itself.
    """
    return min(invariances, key=lambda value: _INVARIANCE_RANK.get(value, 0), default=enums.TransformInvariance.ISOMETRY.value)


def weakest_validity(validities: Iterable[str]) -> str:
    """How known a composition is: the weakest of the claims composed.

    Empty is VALIDATED, the top of the order and the honest answer for a path with no steps --
    a space's placement in itself is exact by construction. "No path at all" is a different
    statement and not this function's to make: a caller that found no path says UNKNOWN
    itself, because the absence of a path is a fact about the caller's search.
    """
    return min(validities, key=lambda value: _VALIDITY_RANK.get(value, 0), default=enums.PlacementValidityChoices.VALIDATED.value)


def _scale_invariance(scale: list[float]) -> str:
    """SIMILARITY when one factor stretches every axis, AFFINE when the factors differ.

    The only number this module reads, and it reads it only for equality: an isotropic scale
    keeps angles and length ratios (a circle stays a circle), an anisotropic one -- a z step
    that is not the xy pixel size, which is the ordinary microscopy case -- keeps neither, and
    reporting SIMILARITY there would tell a client an angle transfers when it does not.
    """
    return enums.TransformInvariance.SIMILARITY.value if len(set(scale)) <= 1 else enums.TransformInvariance.AFFINE.value


def _params_invariance(params: dict) -> str:
    """The class of the map a childless composite carries in its own parameters.

    A min, not a first match: `build_registration_edge` admits `scale`, `translation` and
    `affine` on one BY_DIMENSION, so an anisotropic scale riding beside a translation must
    still read AFFINE. No parameters at all is a pure axis selection: an identity on the axes
    the edge names.
    """
    stated = [invariance for key, invariance in _PARAM_INVARIANCE.items() if key in params]
    if "scale" in params:
        stated.append(_scale_invariance(params["scale"] or []))
    return weakest_invariance(stated)


def invariance_of(edge: "models.Transformation") -> str:
    """Which geometric properties survive this edge's map.

    Kind, not numerics -- the discipline :func:`is_invertible` keeps, for the same reason. The
    one number it reads is a SCALE's vector, and only to ask whether its entries are equal,
    which is the whole difference between a shape at another size and a shape sheared.

    An AFFINE reads AFFINE even when its matrix is rigid: separating a rotation from a shear
    needs an SVD, and that is exactly where :func:`is_invertible` stops too, declining to
    catch a singular affine.

    A composite is the weakest of its children. A *childless* composite is the one place this
    must NOT mirror `is_invertible`, which answers True there because invertibility does not
    depend on which parameters ride along; invariance is nothing but that, and a childless
    BY_DIMENSION carrying an `affine` is the ordinary shape of a registration crossing a rank
    boundary. Its parameters ARE its children, and are read as such.

    An unrecognised kind is NONE -- the bottom -- so a future kind fails safe rather than
    claiming rigidity.
    """
    if edge.kind in _COMPOSITE_KINDS:
        children = list(edge.children.all())
        if children:
            return weakest_invariance(invariance_of(child) for child in children)
        return _params_invariance(edge.params or {})
    if edge.kind == enums.TransformKindChoices.SCALE.value:
        return _scale_invariance((edge.params or {}).get("scale") or [])
    return _INVARIANCE_BY_KIND.get(edge.kind, enums.TransformInvariance.NONE.value)


def is_reverse_traversable(edge: "models.Transformation") -> bool:
    """Whether a path may walk this edge against its stored direction.

    The BFS is happy to traverse an edge backwards and hand the client an
    ``inverted: true`` step to undo. That is only honest for a map that *has* an
    inverse, and there are two ways not to have one.

    It may collapse dimensions: an edge whose two sides do not have the same number of
    axes states nothing about where the missing ones came from -- placing a (c,y,x)
    dataset into a (t,z,y,x) world says nothing about `t` and `z` -- so inverting it
    would mean inverting a non-square matrix.

    Or its *kind* may have no inverse to give, at any rank: a FIELD, a declared
    non-correspondence. Rank alone was a sufficient rule only for as long as every
    writable kind happened to be invertible, which stopped being true the moment a
    field became writable.
    """
    return is_traversable(edge) and is_invertible(edge) and len(edge_axis_names(edge, "input")) == len(edge_axis_names(edge, "output"))


#: The kinds whose form is square by construction, so a rank change is not something they can
#: express -- one number per input axis (`_PER_AXIS_KINDS`, spelled out below rather than
#: referenced because that tuple is written for a different check further down), or a
#: pass-through. `assert_edge_rank` refuses to write one across a rank boundary, and
#: `coords.step_forms` raises for one that got written anyway ("only BY_DIMENSION states a rank
#: change"), which is the case `is_condensable` catches. Everything absent here -- the
#: composites, and the whole-matrix kinds -- states its own output rank in its parameters.
_SQUARE_FORM_KINDS = frozenset(
    {
        enums.TransformKindChoices.IDENTITY.value,
        enums.TransformKindChoices.SCALE.value,
        enums.TransformKindChoices.TRANSLATION.value,
    }
)


def is_condensable(edge: "models.Transformation") -> bool:
    """Whether this edge, walked forwards, composes into a closed affine form.

    The write-time twin of what :func:`condense_path` discovers at read time. Reachability is
    not placement: :func:`is_traversable` refuses only UNMAPPABLE, so a walk happily crosses a
    FIELD and hands back a path that `asAffine` then cannot condense -- a layer accepted at
    creation that no renderer can place. This is the predicate that closes that gap, and it
    mirrors the exact two places `coords.step_forms` raises.

    **Kind, through the wrapper recursion -- read off the invariance.** `step_forms` has
    exactly two kinds with no closed form, and says so: a FIELD, whose map is the values of an
    array, and an UNMAPPABLE, which denies the correspondence outright. In the lattice RFC-8
    writes down those are exactly DIFFEOMORPHIC and NONE, so AFFINE-and-stronger *is* the
    condensable set, and :func:`invariance_of` already does every hard part -- it recurses into
    a composite's children, reads a childless composite's params as its children, and falls
    back to NONE for a kind it does not know, which fails safe in this direction too. A third
    copy of that recursion would be a third chance to disagree with it. **The line to revisit
    when a kind is added** is therefore this one: a new kind is condensable exactly when
    `_INVARIANCE_BY_KIND` puts it at AFFINE or above.

    Note the question is about `step_forms`/`compose_forms`, **not** about `coords.has_matrix`,
    whose `MATRIX_KINDS` excludes BY_DIMENSION -- the shape of every ordinary registration
    crossing a rank boundary. Asking the fixed-rank composer here would refuse the common case.

    **Rank, for the one edge the kind gate cannot see.** `assert_edge_rank` checks a SCALE or a
    TRANSLATION against the *input* rank only, so an edge carrying one number per input axis
    between systems of different rank is writable -- and `step_forms` raises for it ("only
    BY_DIMENSION states a rank change") though its invariance reads ISOMETRY. Only
    BY_DIMENSION and the whole-matrix kinds state their own output rank.

    Backwards needs no twin: :func:`is_reverse_traversable` already requires
    :func:`is_invertible`, which excludes FIELD and UNMAPPABLE, and equal rank on the two
    sides -- so every step a walk offers inverted is square and closed-form already.
    """
    if _INVARIANCE_RANK.get(invariance_of(edge), 0) < _INVARIANCE_RANK[enums.TransformInvariance.AFFINE.value]:
        return False

    if edge.kind not in _SQUARE_FORM_KINDS:
        return True
    return len(edge_axis_names(edge, "input")) == len(edge_axis_names(edge, "output"))


def assert_field_is_dereferenceable(field: "models.CoordinateSystem") -> None:
    """Refuse a FIELD whose map is not something standing in it could dereference.

    A FIELD's map is the *contents* of what lives in the system it names, and what earns it
    a place as an edge is that **standing somewhere in that space yields an id**. Two
    substrates satisfy that, and they differ only in where the answer was materialised:

    - an **array**, whose pixels are the map -- a label mask painted with nucleus ids;
    - a **mesh collection**, whose ids ride on the geometry rows, so a client that picked a
      surface is already holding one and samples nothing;
    - a **network collection**, the same sentence over a wireframe: its object ids ride on
      the geometry rows and the object catalog, so picking a filament is already a
      dereference.

    This is the geometry/record-land boundary, and it is checked where the edge is written
    rather than left until someone probes it: **a map out of a table is not a FIELD**, it is
    a foreign key, and it belongs on ``Column.references``. That one needs a *row*
    before it can answer, which is exactly the line this function draws. RFC-7 argues the
    why ("References, not joins"); `docs/field-vs-references.md` works the cases; this is
    where it is enforced.

    Concretely, in a nuclei experiment: the mask painted with nucleus ids *is* a map from
    pixels to nuclei, so it is a FIELD. "This nucleus belongs to track 17" is not -- nothing
    in any space holds it, it is a value in a row -- so it is a reference. Paint a second
    mask whose pixels are track ids and it becomes a FIELD again; what decides is whether
    the answer was materialised where you can stand, never how the relation reads in English.

    Deliberately **not** a store check. A zarr store is attached after its array row exists, so
    "no store yet" is a read-time concern (:func:`core.logic.attribute_plans.resolve_field_store`);
    refusing it here would make the order of two unrelated writes load-bearing.
    """
    if next(iter(field.data_arrays.all()[:1]), None) is not None:
        return
    if next(iter(field.datasets.all()[:1]), None) is not None:
        return
    if next(iter(field.mesh_collections.all()[:1]), None) is not None:
        return
    if next(iter(field.network_collections.all()[:1]), None) is not None:
        return
    if next(iter(field.lenses.all()[:1]), None) is not None:
        raise ValueError(
            f"Only a lens lives in coordinate system '{field.name}': a lens is a selection over a dataset and owns no array, so there is nothing to sample. Name the dataset's own system as the field."
        )
    raise ValueError(
        f"Nothing carrying ids lives in coordinate system '{field.name}', so standing in it dereferences nothing and it cannot be a FIELD's map. A FIELD's map is the contents of an array, of a mesh collection's geometry, or of a network collection's geometry. "
        "A map out of a *table* is not a FIELD edge -- it does no coordinate work, so no walk can use it: declare it as a column reference (Column.references) instead."
    )


def product_space_tables(tables: "Iterable[models.TableDataset]") -> set[int]:
    """Which of ``tables`` identify an axis themselves -- the product spaces -- in one query.

    The batched form of :func:`identified_axes`, and the one every *loop* must use. Whether a
    table is a product space is a fact about its columns, so asking it per table is an N+1 that
    grows with the graph rather than with the join depth -- the shape
    ``tests/test_column_options.py::test_the_walk_costs_the_same_however_many_columns_there_are``
    exists to catch, and did.

    Returns primary keys rather than names, because the callers hold tables and want a
    membership test, and because a name is not unique.
    """
    identifiers = [table.pk for table in tables]
    if not identifiers:
        return set()
    # Either no-edge identification makes a product space: a `references` pair (the contact-map
    # case) and a `node_references` pair (a network's node or edge table) both mean a row is
    # addressed by more ids than the one the FIELD edge supplies -- exactly what the callers
    # drop such tables for. Still one query.
    return set(
        models.Column.objects.filter(
            table_id__in=identifiers,
            role=enums.ColumnRoleChoices.COORDINATE.value,
            axis_type=enums.AxisTypeChoices.INDEX.value,
        )
        .filter(Q(references__isnull=False) | Q(node_references__isnull=False))
        .values_list("table_id", flat=True)
    )


def identified_axes(system: "models.CoordinateSystem") -> set[str]:
    """The axes of ``system`` that something other than the edge landing on it identifies.

    **Every axis of a FIELD's target must be accounted for** -- by the edge (consumed, passed
    through, or produced) or by its own identification. Until product spaces, the second half
    was empty and the rule reduced to "the edge accounts for all of them", which is what
    :func:`assert_edge_rank` used to say outright.

    Today two things identify a table's axis, both on INDEX coordinate columns only (an INDEX
    axis's values are already ids, so naming what it enumerates is what the enumeration is
    *of*, not a second map competing with the first): a ``references`` naming the table its
    positions enumerate, and a ``node_references`` naming the network collection whose node
    ids they are -- scoped by the sibling axis that collection's objects key, which is what
    keeps a node table's edge producing exactly the one object axis.

    One definition, used by both the rank check and the ``keyedBy`` axis split, because two
    copies of this would be a table the split accepts and the rank check then refuses.

    **Two substrates, one relation**, exactly as a FIELD edge has two: a table says it with a
    COORDINATE column's ``references``, a sparse dataset with a :class:`SparseAxisReference`,
    because a matrix has no columns to hang it on. The sentence is the same either way -- *the
    values along this axis identify rows of that table* -- so it is answered here once rather
    than branched on at every call site.

    Returns an empty set for a system that owns neither, which is every array-backed one: a
    pixel grid's axes are identified by being a grid.
    """
    table = next(iter(system.table_datasets.all()[:1]), None)
    if table is not None:
        return {
            column.name
            for column in table.columns.all()
            if column.role == enums.ColumnRoleChoices.COORDINATE.value
            and column.axis_type == enums.AxisTypeChoices.INDEX.value
            and (column.references_id is not None or column.node_references_id is not None)
        }

    sparse = next(iter(system.sparse_datasets.all()[:1]), None)
    if sparse is not None:
        return {reference.axis for reference in sparse.axis_references.all()}

    return set()


def assert_field_produces(*, field: "models.CoordinateSystem", output_axes: list[str]) -> None:
    """Enforce a FIELD's value axis against the rank the edge says its values produce.

    The value axis is deliberately **elidable**. A label mask is a plain (y,x) array whose
    one value is an object id; making it carry a length-1 COORDINATE axis to satisfy a
    schema would add a phantom dimension no reader wants and no writer stores. So absent
    means scalar, and scalar produces exactly one axis. A field that produces more than one
    must say so with a value axis -- whose type is, in the same breath, what says whether
    its numbers are positions or offsets.

    The value axis' *length* is not checked: an ``Axis`` row carries no size (shape lives on
    the DataArray), so "this COORDINATE axis has 3 positions, so the edge must produce 3
    axes" is not answerable from here. The scalar case is, and it is the one a mask hits.
    """
    value_axes = [axis for axis in field.axes.all() if axis.type in _VALUE_AXIS_TYPES]

    if len(value_axes) > 1:
        raise ValueError(
            f"A field's values are one thing, so its system carries at most one value axis, but '{field.name}' has {[axis.name for axis in value_axes]}. Two value axes would be two answers to 'what are these numbers'."
        )

    if not value_axes and len(output_axes) != 1:
        raise ValueError(
            f"Field '{field.name}' carries no value axis, so its values are scalar and produce exactly one axis, but this edge says they produce {output_axes}. Give the field a COORDINATE (positions) or DISPLACEMENT (offsets) value axis to produce more than one."
        )


#: The axis types a per-index selector may name: the ones you index rather than measure. A
#: SPACE axis is refused because "at x = 3.7" is not a question a piecewise-constant map
#: answers -- an array whose values are the map (FIELD) answers it exactly, and already exists.
_SELECTABLE_AXIS_TYPES: frozenset[str] = frozenset(
    {
        enums.AxisTypeChoices.CHANNEL.value,
        enums.AxisTypeChoices.TIME.value,
        enums.AxisTypeChoices.INDEX.value,
        enums.AxisTypeChoices.MICROTIME.value,
        enums.AxisTypeChoices.SPECTRUM.value,
    }
)


def selector_admits(edge: "models.Transformation", at: dict[str, int] | None, *, admit_scoped: bool = False) -> bool:
    """Whether a query standing at ``at`` may cross this edge.

    The one reader of the selector convention, so every walk and the validator agree about what
    a scoped edge means. Four cases, and the middle two are the load-bearing ones:

    * **No selector** -- the edge holds everywhere and is always admitted. Every edge written
      before this column existed is this case, which is why adding selectors changed no answer.
    * **A selector, and the query fixed no coordinate** (``at`` is None or silent about the axis)
      -- **refused**. This is the honest answer rather than the convenient one: where the data
      sits genuinely depends on the channel, so a query that has not said which channel has no
      single answer to be given. Admitting it "just for now" would pick one arbitrarily, which is
      the same class of bug as the pk-ordered tie-break `_bfs_tree` exists to have fixed.
    * **A selector and a matching coordinate** -- admitted.
    * **``admit_scoped``** -- admitted whatever ``at`` says, because the caller is asking a
      different question. "Where is this?" needs a coordinate. "Is this placed *at all*?" does
      not, and must not: a dataset registered per channel is registered, and a caller deciding
      whether to refuse a layer, or which of two reasons an unplaced layer has, would otherwise
      be told a piecewise placement is no placement. Never use it to compose a map -- the edge
      it lets through holds only where its selector says, and a path built through one without
      fixing that coordinate would be asserting the correction everywhere.
    """
    selector = edge.selector
    if not selector:
        return True
    if admit_scoped:
        return True
    if not at:
        return False
    axis = selector.get("axis")
    return axis in at and at[axis] == selector.get("index")


def assert_selector(selector: dict | None, input_system: "models.CoordinateSystem") -> None:
    """Reject a selector that names no axis of the input system, or one it cannot index.

    Scoped to the **input** side deliberately: the selector says where in the *source* the map
    applies, which is the coordinate a caller is standing at when it asks. The output side has no
    say -- a per-channel correction into a world with no channel axis is exactly the ordinary
    case, and requiring the axis on both sides would refuse it.
    """
    if selector is None:
        return
    if not isinstance(selector, dict) or set(selector) != {"axis", "index"}:
        raise ValueError("A selector is {'axis': <name>, 'index': <int>} and takes no other keys: it names one discrete position along one axis of the input system.")

    axis_name, index = selector.get("axis"), selector.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError(f"A selector's `index` is a non-negative whole number -- a position along an axis -- but got {index!r}.")

    axes = {axis.name: axis for axis in input_system.axes.all()}
    if axis_name not in axes:
        raise ValueError(f"A selector names axis '{axis_name}', which coordinate system '{input_system.name}' does not have (its axes are {sorted(axes)}).")

    axis_type = axes[axis_name].type
    if axis_type not in _SELECTABLE_AXIS_TYPES:
        raise ValueError(
            f"A selector names '{axis_name}', a {axis_type} axis, which is measured rather than indexed -- 'at x = 3.7' is not a position a piecewise map has a value at. "
            "Use FIELD, whose values are the map, for a correction that varies continuously along a spatial axis."
        )


def _axis_pairs(
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
    input_names: list[str],
    output_names: list[str],
) -> list[tuple["models.Axis", "models.Axis"]]:
    """The axis rows an edge puts in correspondence, paired positionally by the two name lists.

    Positional, not by name, because that is what every correspondence in this module already
    means: a BY_DIMENSION's ``inputAxes[i]`` maps to its ``outputAxes[i]``, and an IDENTITY's
    lists are equal so the two readings coincide. Rows the systems do not have are skipped
    rather than raising -- the existence check is a separate, earlier rule with its own message.
    """
    by_name_in = {axis.name: axis for axis in input_system.axes.all()}
    by_name_out = {axis.name: axis for axis in output_system.axes.all()}
    pairs = []
    for source, target in zip(input_names, output_names):  # noqa: B905 - ragged is impossible here; the length check runs first
        a, b = by_name_in.get(source), by_name_out.get(target)
        if a is not None and b is not None:
            pairs.append((a, b))
    return pairs


def _assert_units_interchangeable(
    *,
    kind: str,
    pairs: list[tuple["models.Axis", "models.Axis"]],
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
) -> None:
    """Refuse a number-free map between axes whose units are not the same unit.

    **Only for the kinds that carry no magnitudes of their own.** A SCALE, TRANSLATION, AFFINE or
    ROTATION states its own numbers, and whether the author already folded the 1000 in is not
    knowable from the row -- `core.logic.coords._pass_through_factor` declines to guess there for
    exactly this reason, and so does this. An IDENTITY has no such escape: it asserts the two
    spaces *are* the same, so a micrometre axis facing a nanometre one is the claim that one
    nanometre is one micrometre.

    That claim is not merely wrong, it is wrong *twice, differently*. The axis-keyed composer
    (`step_forms` -> `compose_forms`, behind `Layer.asAffine`) applies the 1000; the fixed-rank
    one (`to_matrix` -> `compose`, behind the annotation bounding-box walk) has no units in its
    signature at all and applies 1. One edge, two stored answers, three orders of magnitude
    apart. Refusing the edge closes the divergence at its source rather than teaching a second
    composer about units.

    The repair is to say what was meant: a SCALE of 1000, which is a map that carries numbers and
    is therefore allowed to relate the two.
    """
    mismatched = [f"'{a.name}' ({a.unit}) -> '{b.name}' ({b.unit})" for a, b in pairs if not coords_logic.units_are_interchangeable(a.unit, b.unit)]
    if mismatched:
        raise ValueError(
            f"An {kind} transformation carries no numbers, so it claims the two spaces measure in the same units -- but '{input_system.name}' and '{output_system.name}' disagree on {', '.join(mismatched)}. "
            "Use SCALE, stating the conversion factor, for a map between two different units; 'a.u.' on either side declines the claim and is always accepted."
        )


def _assert_axis_types_correspond(
    *,
    kind: str,
    pairs: list[tuple["models.Axis", "models.Axis"]],
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
) -> None:
    """Refuse a correspondence that maps one kind of axis onto a different kind.

    The named-subset checks above establish that the axes *exist* and pair one for one; none of
    them looks at what the axes are. So ``inputAxes: ["c"], outputAxes: ["z"]`` was accepted, and
    it silently maps a channel index onto a spatial position -- a statement no downstream reader
    can catch, because by then it is only two names and a matrix.

    Type is the honest invariant here rather than name: ``zyx`` versus ``ZYX`` versus ``row/col``
    are all legitimate namings of the same spatial axes, so requiring equal names would refuse
    real registrations. Requiring equal *types* refuses only the maps that change what the
    coordinate means.

    **INDEX is the deliberate exception, on either side.** An enumeration has no metric and no
    place -- that is its definition -- so where its objects sit is not a property it carries but
    exactly the thing a registration establishes. Mapping an object-id axis onto a spatial one is
    the ordinary product-space placement (a table of objects laid into a physical space), and it
    is the same case `_METRIC_KINDS`' INDEX guard above is careful *not* to refuse. Requiring
    equal types here without this exemption would refuse it, which is how the first version of
    this check broke `test_a_field_step_errors_and_names_the_edge`.
    """
    index = enums.AxisTypeChoices.INDEX.value
    crossed = [f"'{a.name}' ({a.type}) -> '{b.name}' ({b.type})" for a, b in pairs if a.type != b.type and index not in (a.type, b.type)]
    if crossed:
        raise ValueError(
            f"A {kind} transformation puts axes in correspondence, but {', '.join(crossed)} relates two different kinds of axis on '{input_system.name}' and '{output_system.name}'. "
            "A channel index is not a position and a time is not a length; a map between them is not a coordinate transformation. Use FIELD (its values are the map) or UNMAPPABLE (nothing corresponds)."
        )


def _assert_epochs_agree(input_system: "models.CoordinateSystem", output_system: "models.CoordinateSystem") -> None:
    """Refuse to relate two clocks that start at different instants.

    ``CoordinateSystem.epoch`` states that ``wall_clock = epoch + t * unit``. Nothing in either
    composer or either walk reads it -- so composing a path across two spaces whose epochs differ
    silently treats their ``t = 0`` as the same instant, and a 09:00 acquisition aligned against
    an 11:00 one is two hours wrong with no error anywhere.

    Refused rather than composed, deliberately. Composing the offset would mean an edge whose
    effective parameters depend on a column *neither endpoint's parameters mention*, which is the
    same "a fact stored somewhere no query can find it" this model exists to avoid. Stating the
    offset as a TRANSLATION on the time axis puts it on the edge, where every reader already
    looks. Two spaces that share an epoch, or where either declines to name one, are unaffected.
    """
    left, right = input_system.epoch, output_system.epoch
    if left is None or right is None or left == right:
        return
    has_clock = any(axis.type == enums.AxisTypeChoices.TIME.value for axis in input_system.axes.all()) and any(axis.type == enums.AxisTypeChoices.TIME.value for axis in output_system.axes.all())
    if not has_clock:
        return
    raise ValueError(
        f"'{input_system.name}' and '{output_system.name}' both carry a time axis but anchor it to different instants ({left.isoformat()} and {right.isoformat()}), so relating them without saying so would assert that those two instants are the same moment. "
        "State the offset as a TRANSLATION on the time axis, or give the two spaces the same epoch."
    )


def assert_edge_rank(
    *,
    kind: str,
    params: dict,
    input_axes: list[str] | None,
    output_axes: list[str] | None,
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
    subset_axes: tuple[list[str], list[str]] | None = None,
) -> None:
    """Enforce that an edge's parameters have the rank its endpoints imply.

    None of this was checked before: a three-entry scale into a four-axis world was
    written without complaint, and `to_matrix` then wrote its last entry into the
    homogeneous corner of the matrix -- which does not raise, it just quietly scales
    everything. The endpoints already say what the rank must be, so an edge that
    disagrees with them is not a judgement call.

    UNMAPPABLE is the one kind with no rank to disagree with. It maps nothing, so there
    is nothing for a rank to be the rank *of*, and relating a (c,y,x) image to a
    one-axis table of objects is not an error to be caught but the entire point.

    ``subset_axes`` exists for one caller: a **wrapper child**. A child of a
    BY_DIMENSION carries parameters over the axes its *parent* names -- `_sub_matrix`
    composes them at ``len(acts_on_input)`` and `_by_dimension_forms` labels the rows by
    ``acts_on_output`` -- but the child's own kind is SCALE or TRANSLATION, so every check
    below would otherwise answer to the whole system. It is given the parent's two ordered
    axis lists rather than a pair of counts, because *three* of those checks read the names
    and not just how many there are: the equal-rank rule, the ROTATION square rule and the
    same-names rule. Held to the full systems, a two-entry scale over ``["y", "x"]`` inside
    a BY_DIMENSION relating ``(c,y,x)`` to ``(t,z,y,x)`` would be refused three times over --
    for a rank it does not answer to, and for names it does not touch -- which is a
    regression manufactured by the fix, on the exact shape BY_DIMENSION exists to express.
    The child cannot state the subset itself: only its parent's axis lists can, and they
    are not its own. See `core.mutations.transformation._rank_endpoints`.
    """
    if kind == enums.TransformKindChoices.UNMAPPABLE.value:
        return

    input_names = [axis.name for axis in input_system.axes.all()]
    output_names = [axis.name for axis in output_system.axes.all()]

    # A wrapper child answers to its parent's named subset, not to the endpoints it borrowed
    # from that parent -- so the names every check below compares are the subset's, in the
    # parent's order. Rebound here, once, rather than at each of the four call sites that
    # would otherwise have to remember which of the two lists it meant.
    if subset_axes is not None:
        input_names, output_names = list(subset_axes[0]), list(subset_axes[1])

    # Two clocks anchored to different instants cannot be related by an edge that says nothing
    # about the offset. Checked for every kind, before the per-kind branches: it is a fact about
    # the two *spaces*, not about the map, so no kind is exempt from it.
    _assert_epochs_agree(input_system, output_system)

    # An INDEX axis has no metric -- that is its definition, not an omission -- so the kinds
    # that do arithmetic on a coordinate mean nothing over it. Checked here rather than left
    # to the rank check below, which would happily accept `scale: [2.0]` on a space of object
    # ids and write "object 3 x 2 = object 6" without complaint.
    if kind in _METRIC_KINDS:
        # Scanned against `input_names`/`output_names` rather than the systems' whole axis
        # lists, which is the same subset rule the checks below follow and matters for the
        # same reason: a SCALE child under a BY_DIMENSION naming ["y", "x"] does no
        # arithmetic on the `object_id` axis its parent left alone, and refusing it for one
        # would refuse the ordinary product-space registration -- an INDEX axis of objects
        # plus two SPACE axes, placed into a purely spatial physical space. Where there is no
        # subset the two lists *are* the systems' axes, so this is a no-op.
        indexed = [
            f"'{axis.name}' on '{system.name}'"
            for system, names in ((input_system, input_names), (output_system, output_names))
            for axis in system.axes.all()
            if axis.type == enums.AxisTypeChoices.INDEX.value and axis.name in names
        ]
        if indexed:
            raise ValueError(
                f"A {kind} transformation does arithmetic on a coordinate, but {', '.join(indexed)} is an INDEX axis, which has no metric: the distance between object 3 and object 4 means nothing, so scaling or offsetting it does too. Relate an index space with FIELD (its values are the map) or UNMAPPABLE (nothing corresponds)."
            )

    if kind == enums.TransformKindChoices.IDENTITY.value:
        # IDENTITY carries no parameters, so nothing below would check it -- and it is the
        # default for a derivation, where it means "the new pixels ARE the old pixels".
        # Between systems whose axes differ that is not an identity at all, it is a
        # rank-changing claim wearing an identity's clothes. A derivation that drops or
        # adds an axis (a projection) is a BY_DIMENSION naming the axes it keeps.
        if input_names != output_names:
            raise ValueError(f"An IDENTITY transformation says the two spaces are the same, but '{input_system.name}' has axes {input_names} and '{output_system.name}' has {output_names}. Use BY_DIMENSION, naming the axes it acts on, for a map that drops or reorders axes.")
        _assert_units_interchangeable(kind=kind, pairs=_axis_pairs(input_system, output_system, input_names, output_names), input_system=input_system, output_system=output_system)
        return

    if kind == enums.TransformKindChoices.FIELD.value:
        # A FIELD consumes input axes and produces output axes out of the array's values;
        # the axes it does not name pass through by name. That is the entire map, so the
        # endpoints are checkable against it exactly -- no convention, no elided component.
        # Deliberately NOT the one-for-one rule below: a FIELD is many-to-one on purpose,
        # (y,x) collapsing to one object id.
        if not input_axes or not output_axes:
            raise ValueError("A FIELD transformation must name the axes it consumes in `inputAxes` and the axes its values produce in `outputAxes`. That naming IS the map: the axes it does not consume pass through by name.")
        for names, axes, side, system in ((input_names, input_axes, "input", input_system), (output_names, output_axes, "output", output_system)):
            if len(set(axes)) != len(axes):
                raise ValueError(f"A FIELD transformation names the {side} axis {axes} more than once")
            unknown = [axis for axis in axes if axis not in names]
            if unknown:
                raise ValueError(f"A FIELD transformation names {side} axes {unknown} that do not exist on coordinate system '{system.name}' (its axes are {names})")
        implied = [axis for axis in input_names if axis not in set(input_axes)] + list(output_axes)
        # Every axis of the target has to be accounted for -- but not necessarily by this edge.
        # An axis the target identifies itself (an INDEX coordinate naming the table its
        # positions enumerate) is one the *reader* selects a position along, so the edge says
        # nothing about it and is not expected to. See `identified_axes`.
        identified = identified_axes(output_system)
        overlap = sorted(set(implied) & identified)
        if overlap:
            raise ValueError(
                f"A FIELD transformation over '{output_system.name}' names {overlap}, which that space already identifies by `references`. An axis is accounted for once: either the edge supplies it or its own declaration does, and two answers to 'what are these positions' is the ambiguity `references` on a coordinate exists to avoid."
            )
        accountable = sorted(set(output_names) - identified)
        if sorted(implied) != accountable:
            unaccounted = "" if not identified else f" ('{output_system.name}' identifies {sorted(identified)} by `references`, which this edge is not expected to supply)"
            raise ValueError(
                f"A FIELD transformation consuming {input_axes} of '{input_system.name}' {input_names} and producing {list(output_axes)} implies the axes {sorted(implied)}, but '{output_system.name}' has {accountable} to account for{unaccounted}. The axes it does not consume pass through by name."
            )
        return

    if kind in (enums.TransformKindChoices.BY_DIMENSION.value, enums.TransformKindChoices.MAP_AXIS.value):
        if not input_axes or not output_axes:
            raise ValueError(f"A {kind} transformation must name the axes it acts on, in `inputAxes` and `outputAxes`. That naming IS the map: the axes it does not name are the ones it leaves untouched.")
        if len(input_axes) != len(output_axes):
            raise ValueError(f"A {kind} transformation maps its input axes onto its output axes one for one, but was given {len(input_axes)} ({input_axes}) and {len(output_axes)} ({output_axes})")
        for names, axes, side, system in ((input_names, input_axes, "input", input_system), (output_names, output_axes, "output", output_system)):
            if len(set(axes)) != len(axes):
                raise ValueError(f"A {kind} transformation names the {side} axis {axes} more than once")
            unknown = [axis for axis in axes if axis not in names]
            if unknown:
                raise ValueError(f"A {kind} transformation names {side} axes {unknown} that do not exist on coordinate system '{system.name}' (its axes are {names})")
        if kind == enums.TransformKindChoices.MAP_AXIS.value and sorted(input_names) != sorted(output_names):
            # A permutation relabels; it does not reshape. `permutation_matrix` synthesizes
            # the matrix over the *input* system's axis order and sends each axis to
            # `mapping.get(axis, axis)` -- so an axis name the input system does not have is
            # not a permutation of anything, and it raises NonAffineTransformError deep in
            # the composer, at read, in a message about a matrix rather than about the edge
            # somebody authored. The condition that makes that function total is exactly
            # this one, so it is checked here, where the author can still fix it. A map
            # between genuinely different axis sets is a BY_DIMENSION.
            raise ValueError(
                f"A MAP_AXIS transformation permutes the axes of one coordinate vector, so both systems carry the same axis names -- but '{input_system.name}' has {sorted(input_names)} and '{output_system.name}' has {sorted(output_names)}. "
                "Use BY_DIMENSION, naming the axes it acts on, to relate two different sets of axes."
            )

        # The named pairs must relate the same *kind* of coordinate. Checked after the
        # existence and one-for-one rules above, so the pairing this reads is already known
        # to be well formed. Units are deliberately not checked here: a BY_DIMENSION's named
        # axes carry its parameters, so it is one of the kinds that states its own magnitudes
        # (see `_assert_units_interchangeable`), and its *unnamed* pass-through axes are
        # already converted correctly by `_by_dimension_forms` rather than being a divergence
        # to close.
        _assert_axis_types_correspond(kind=kind, pairs=_axis_pairs(input_system, output_system, input_axes, output_axes), input_system=input_system, output_system=output_system)

        # A BY_DIMENSION's optional parameters act on the *named* axes -- that is the
        # whole point of naming them -- so the rank they are checked against is the
        # subset's, not the system's. A MAP_AXIS never carries parameters at all: it is a
        # pure permutation whose matrix is synthesized from the axis lists at read time.
        rank_in, rank_out = len(input_axes), len(output_axes)
    else:
        rank_in, rank_out = len(input_names), len(output_names)
        # A *per-axis* kind carries one number per input axis, so the matrix it lowers to is
        # square at the input's rank and cannot reach a different output rank -- and only
        # the input rank is checked below, so `scale: [2, 2]` from a (y,x) grid into a
        # (t,z,y,x) world used to be written without complaint. It surfaced far away:
        # `to_matrix` raises NonAffineTransformError, which the extent walk swallows into
        # ExtentState.NON_AFFINE, leaving the source permanently unboundable in every
        # spatial query over that space, with no error ever reaching its author.
        #
        # Deliberately not AFFINE or ROTATION, which carry a whole matrix: theirs is
        # M x (N+1) and rectangular *by design*, so a rank-changing one is well-defined and
        # is exactly what the rank check below already holds them to.
        if kind in _PER_AXIS_KINDS and rank_in != rank_out:
            raise ValueError(
                f"A {kind} transformation carries one number per input axis, so the map it describes is square and relates spaces of equal rank -- but '{input_system.name}' has {rank_in} axes {input_names} and '{output_system.name}' has {rank_out} {output_names}. Use BY_DIMENSION, naming the axes it acts on, to place data into a space of a different rank."
            )

        # A ROTATION carries a whole matrix, so it escapes the per-axis rule above and lands
        # in the rectangular M x (N+1) check below -- which happily accepts a "rotation"
        # between a 2-axis grid and a 3-axis world. A rotation is square by *definition*: it
        # is an element of the orthogonal group of one space, and there is no such thing
        # between two spaces of different rank. Its own guard rather than an entry in
        # `_PER_AXIS_KINDS`, whose message is about one-number-per-axis and would read
        # wrongly here.
        if kind == enums.TransformKindChoices.ROTATION.value and rank_in != rank_out:
            raise ValueError(
                f"A ROTATION is a rotation *of* a space, so it relates a space to itself and its matrix is square -- but '{input_system.name}' has {rank_in} axes {input_names} and '{output_system.name}' has {rank_out} {output_names}. "
                "Use AFFINE for a rank-changing matrix, or BY_DIMENSION to rotate the axes the two spaces share."
            )

    # A per-axis kind binds its i-th number to the i-th axis of the *input* system, and
    # `_forms_from_matrix` then labels row i with output axis i. So the two systems' axis
    # orders are not incidental to it: they are the whole of what the numbers mean. A SCALE
    # from a (z,y,x) grid into a space declared (x,y,z) was accepted, and the factor meant for
    # z landed on x -- no error at write, none at read, and `compose_forms` carried the
    # transposition into every AxisExtent, asAffine and inView answer downstream.
    #
    # IDENTITY already holds itself to exactly this (ordered equality, above) and MAP_AXIS to
    # the weaker set equality, because a permutation is what MAP_AXIS is *for*. This closes the
    # gap between them.
    #
    # Deliberately NOT applied to AFFINE. `_forms_from_matrix` labels an affine's rows by the
    # output axes and its columns by the input axes -- both orders are the author's explicit
    # statement, and a rank- and name-changing AFFINE is legal by design (see
    # `test_a_rank_changing_edge_is_not_walked_backwards`). There is no rule to check there,
    # only a guess.
    if kind in _NAME_ORDERED_KINDS and input_names != output_names:
        raise ValueError(
            f"A {kind} transformation carries one number per axis, in the input system's order, so the two systems must name their axes the same way -- but '{input_system.name}' has {input_names} and '{output_system.name}' has {output_names}. "
            "Its numbers would be read against the wrong axes with nothing to say so. Use BY_DIMENSION, which names the axes it acts on and can state a reorder honestly, or MAP_AXIS for a pure permutation."
        )

    for field in ("scale", "translation"):
        vector = params.get(field)
        if vector is not None and len(vector) != rank_in:
            raise ValueError(f"A {kind} transformation's `{field}` has one entry per input axis: expected {rank_in}, got {len(vector)}")

    affine = params.get("affine")
    if affine is not None:
        if len(affine) != rank_out:
            raise ValueError(f"A {kind} transformation's `affine` has one row per output axis: expected {rank_out} rows, got {len(affine)}")
        if any(len(row) != rank_in + 1 for row in affine):
            raise ValueError(f"A {kind} transformation's `affine` rows have one column per input axis plus the translation: expected {rank_in + 1} entries per row, got {[len(row) for row in affine]}")


def write_relation_edge(
    *,
    name: str,
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
    kind: str,
    scale: list[float] | None = None,
    translation: list[float] | None = None,
    affine: list[list[float]] | None = None,
    input_axes: list[str] | None = None,
    output_axes: list[str] | None = None,
    field: "models.CoordinateSystem | None" = None,
    reason: str | None = None,
    value_relation: "enums.ValueRelation | str | None" = None,
    ctx: CreationContext,
) -> "models.Transformation":
    """The one place a client-authored "this came from that" edge is written.

    A derived dataset, a mesh collection and a feature table all say the same kind of
    thing -- *my space, and how it relates to the space I was computed from* -- so they say
    it the same way, and the rank check that catches a projection wearing an identity's
    clothes catches it once for all three.

    A derivation may state any kind a registration may: same union, same guards, one
    delegation. What stays derivation-specific is the validity -- always MANUAL,
    deliberately not a parameter, because a derivation is an authored claim about where
    data came from, never something the server derived or anyone validated.

    ``value_relation`` is the derivation's second, orthogonal statement: what happened
    to the *numbers* (a threshold is spatially IDENTITY with CATEGORIZED values). It
    rides the same row because it is a fact about the same event -- a parallel lineage
    table for it was tried once and deleted (RFC-6).
    """
    return build_registration_edge(
        input_system=input_system,
        output_system=output_system,
        kind=kind,
        name=name,
        scale=scale,
        translation=translation,
        affine=affine,
        input_axes=input_axes,
        output_axes=output_axes,
        field=field,
        reason=reason,
        validity=None,  # defaults to MANUAL inside: an authored claim, never VALIDATED
        value_relation=value_relation,
        ctx=ctx,
    )


def create_collection_system(
    *,
    name: str,
    axes: list,
    owner: "models.MeshCollection | models.NetworkCollection | models.TableDataset | models.AnnotationCollection | None" = None,
    ctx: CreationContext,
) -> "models.CoordinateSystem":
    """The coordinate system a collection owns, with its axes.

    Pixel axes, not calibrated ones: a mesh collection's vertices are in the voxel grid
    they were extracted from, a feature table's rows are enumerated, and an annotation
    collection's shapes are drawn in the grid of whatever it registers into. None carries
    a unit, and a unit is the only thing `create_physical_axes` would add.

    A collection is the one caller whose axes arrive straight from the client, and the
    order it gives is the order they are written in. No axis *type* ordering is required of
    it: `resolve_render_axes` finds the time, channel and phasor axes by type rather than by
    position, so where they sit among the spatial ones changes nothing. What it does read is
    the relative order of the spatial axes, and that is a convention it falls back to rather
    than a rule anything can check -- a collection declaring `x, y, z` gets `x=z`, which is
    item 14 of the proposals doc and is not fixed by ordering by type.
    """
    system = models.CoordinateSystem.objects.create(
        name=name,
        creator=ctx.user,
        organization=ctx.organization,
    )
    create_pixel_axes(system, axes)
    if owner is not None:
        # The collection moves *into* the space. Two writes here rather than one only
        # because the caller already saved the collection to name the space after it.
        owner.coordinate_system = system
        owner.save(update_fields=["coordinate_system"])
    return system


#: The parameters each directly-creatable edge kind requires. BY_DIMENSION requires none:
#: it is the axis *naming* that carries the map, and any parameters it carries act on the
#: axes it names. UNMAPPABLE requires none because it *has* none, and rejects them below.
_PARAMS_BY_KIND: dict[str, tuple[str, ...]] = {
    enums.TransformKind.IDENTITY.value: (),
    enums.TransformKind.SCALE.value: ("scale",),
    enums.TransformKind.TRANSLATION.value: ("translation",),
    enums.TransformKind.AFFINE.value: ("affine",),
    enums.TransformKind.ROTATION.value: ("affine",),
    enums.TransformKind.MAP_AXIS.value: (),
    enums.TransformKind.BY_DIMENSION.value: (),
    enums.TransformKind.FIELD.value: (),
    enums.TransformKind.UNMAPPABLE.value: (),
}

#: The parameters a BY_DIMENSION edge may additionally carry, acting on its named axes.
_OPTIONAL_PARAMS_BY_KIND: dict[str, tuple[str, ...]] = {
    enums.TransformKind.BY_DIMENSION.value: ("scale", "translation", "affine"),
}

#: The kinds whose map is the axis naming itself, so `input_axes`/`output_axes` belong on
#: the row. Every other kind acts on every axis in the systems' own order: stored axis
#: names there would not be ignored, they would silently override what `edge_axis_names`
#: reports as the parameter ordering -- so they are rejected, and never persisted.
_AXIS_KINDS = (
    enums.TransformKind.MAP_AXIS.value,
    enums.TransformKind.BY_DIMENSION.value,
    enums.TransformKind.FIELD.value,
)


def _assemble_edge_params(
    *,
    kind: str,
    noun: str,
    scale: list[float] | None,
    translation: list[float] | None,
    affine: list[list[float]] | None,
    input_axes: list[str] | None,
    output_axes: list[str] | None,
    reason: str | None,
) -> dict:
    """The params dict an edge of ``kind`` stores -- exactly what the kind reads, or an error.

    The GraphQL surface already rejects a parameter that contradicts the kind (the
    transform input is a discriminated union whose members forbid what is not theirs),
    so through the API these raises are unreachable. They are the same contract for the
    callers below it: an internal writer that slips a translation onto a SCALE edge gets
    an error here, not a row whose extra key nothing ever reads.
    """
    supplied = {"scale": scale, "translation": translation, "affine": affine}
    allowed = set(_PARAMS_BY_KIND[kind]) | set(_OPTIONAL_PARAMS_BY_KIND.get(kind, ()))

    stray = sorted(param for param, value in supplied.items() if value is not None and param not in allowed)
    if stray:
        raise ValueError(f"A {kind} {noun} does not read `{stray[0]}`: drop it, or use the kind whose map it is.")

    if (input_axes or output_axes) and kind not in _AXIS_KINDS:
        raise ValueError(f"A {kind} {noun} acts on every axis, in the input system's own order, so it takes no `inputAxes`/`outputAxes`. Use BY_DIMENSION or MAP_AXIS to act on named axes.")

    if reason and kind != enums.TransformKind.UNMAPPABLE.value:
        raise ValueError(f"`reason` belongs to an UNMAPPABLE edge; a {kind} {noun}'s story is its parameters.")

    params: dict = {}
    for param in _PARAMS_BY_KIND[kind]:
        value = supplied[param]
        if value is None:
            raise ValueError(f"A {kind} {noun} requires `{param}`")
        params[param] = value

    for param in _OPTIONAL_PARAMS_BY_KIND.get(kind, ()):
        value = supplied[param]
        if value is not None:
            params[param] = value

    if reason:
        params["reason"] = reason

    assert_edge_values(params, noun=noun)
    return params


def assert_edge_values(params: dict, *, noun: str = "transformation") -> None:
    """Reject parameters that describe a map collapsing an axis.

    The same two rules the transform union's members enforce above the API
    (:func:`~core.inputs.coords.assert_no_collapsed_factors` and
    :func:`~core.inputs.coords.assert_no_collapsed_rows`), held here for the callers below
    it -- the same two-altitude contract ``_assemble_edge_params`` already holds for stray
    parameters, and for the same reason: the union makes a bad value unrepresentable
    through GraphQL, and nothing makes it unrepresentable to an internal writer.

    A ``translation`` has no collapsing value -- every offset, zero included, is a real
    offset -- so it is not checked.

    Three rules, not two, since the singularity check joined them: a matrix may collapse an
    axis without any row of it being zero (``[[1, 1, 0], [1, 1, 0]]``), and that is the case
    :func:`is_invertible` documents itself as unable to catch. It reads the *shape* rather
    than the kind -- this function is handed a params dict and has no kind to branch on --
    which is exactly right: what decides whether the question applies is whether the linear
    part is square, and a rank-changing AFFINE's is not.
    """
    scale = params.get("scale")
    if scale is not None:
        assert_no_collapsed_factors(scale, noun=noun)

    affine = params.get("affine")
    if affine is not None:
        assert_no_collapsed_rows(affine, noun=noun)
        assert_nonsingular_matrix(affine, noun=noun)


def updatable_params(kind: str) -> tuple[str, ...]:
    """The parameter fields a refinement of a ``kind`` edge may touch.

    Derived from the same tables creation reads, so the two gates cannot drift. A kind
    that is not directly creatable (a SEQUENCE wrapper) refines nothing
    here: its parameters live on its children.
    """
    return tuple(
        param
        for param in (*_PARAMS_BY_KIND.get(kind, ()), *_OPTIONAL_PARAMS_BY_KIND.get(kind, ()))
        if param in ("scale", "translation", "affine")
    )

#: The axis types that make an array readable as a FIELD's map: its *value* axis, whose
#: positions enumerate the components of each value. The type is the whole statement --
#: COORDINATE for absolute positions, DISPLACEMENT for offsets -- and it lives here, on the
#: array, rather than on the edge, because it is a property of the data. Two edges reading
#: one field cannot disagree about what its numbers mean.
_VALUE_AXIS_TYPES = (enums.AxisTypeChoices.COORDINATE.value, enums.AxisTypeChoices.DISPLACEMENT.value)

#: The metric kinds: the ones whose parameters do arithmetic on a coordinate. None of them
#: may touch an INDEX axis. An INDEX coordinate is an id -- object 3, row 7 -- and it has no
#: metric by definition, so object 3 x 2 = object 6 is not a wrong number but a meaningless
#: one. `assert_edge_rank` cannot catch it: it checks that a scale vector has one entry per
#: axis, never whether scaling *that* axis means anything.
_METRIC_KINDS = (
    enums.TransformKind.SCALE.value,
    enums.TransformKind.TRANSLATION.value,
    enums.TransformKind.AFFINE.value,
    enums.TransformKind.ROTATION.value,
)

#: The metric kinds carrying one number *per input axis*, as opposed to a whole matrix. The
#: distinction is a rank one: a vector of length N lowers to a square N x N matrix, so such
#: an edge cannot relate spaces of different rank -- while an AFFINE's M x (N+1) is
#: rectangular by design and relates them perfectly well (a (t,z,y,x) world into a (c,y,x)
#: grid is an ordinary authored edge, `test_a_rank_changing_edge_is_not_walked_backwards`).
#: The kinds whose parameters are read against the axis order of both systems, so the two must
#: agree on their axis names *in order*. SCALE and TRANSLATION carry one number per axis;
#: ROTATION is square by definition and its matrix is indexed the same way. AFFINE is
#: deliberately absent -- see the check in `assert_edge_rank` for why.
_NAME_ORDERED_KINDS = (
    enums.TransformKind.SCALE.value,
    enums.TransformKind.TRANSLATION.value,
    enums.TransformKind.ROTATION.value,
)

_PER_AXIS_KINDS = (
    enums.TransformKind.SCALE.value,
    enums.TransformKind.TRANSLATION.value,
)

#: The parameter fields an UNMAPPABLE edge must not carry: it declares that no point of one
#: space corresponds to a point of the other, so a scale on it would assert a correspondence
#: and deny one in the same breath, and nothing downstream would ever read the number.
_FORBIDDEN_ON_UNMAPPABLE = ("scale", "translation", "affine", "input_axes", "output_axes")


def build_registration_edge(
    *,
    input_system: "models.CoordinateSystem",
    output_system: "models.CoordinateSystem",
    kind: "enums.TransformKind | str",
    name: str | None = None,
    scale: list[float] | None = None,
    translation: list[float] | None = None,
    affine: list[list[float]] | None = None,
    input_axes: list[str] | None = None,
    output_axes: list[str] | None = None,
    field: "models.CoordinateSystem | None" = None,
    reason: str | None = None,
    validity: "enums.PlacementValidity | str | None" = None,
    value_relation: "enums.ValueRelation | str | None" = None,
    selector: dict | None = None,
    ctx: CreationContext,
) -> "models.Transformation":
    """Validate and write one edge of the coordinate graph, input -> output.

    The shared core of ``createTransformation`` and ``createCoordinateSystem``: it checks
    the parameters against the kind, forbids a map on an UNMAPPABLE edge, enforces the rank
    the endpoints imply, and writes the row. The systems and the field node are already
    resolved by the caller, so this stays an ORM write rather than a request-scoped one.

    BY_DIMENSION is how a registration crosses a rank boundary: a (c,y,x) dataset placed
    into a (t,z,y,x) world names the axes it acts on (``input_axes=["y","x"]``) and says
    nothing about the world's `t` and `z`. Direction is always forward: input to output.

    Validity defaults to MANUAL, not VALIDATED: an edge that arrived through the API was
    *authored*, which is a different claim from "checked against the data", and the caller
    says VALIDATED only when it was.
    """
    kind = kind.value if hasattr(kind, "value") else kind

    if kind not in _PARAMS_BY_KIND:
        raise ValueError(f"{kind} cannot be created directly. A SEQUENCE wrapper is built by the ingest, which writes its children with it")

    supplied = {"scale": scale, "translation": translation, "affine": affine, "input_axes": input_axes, "output_axes": output_axes}

    if kind == enums.TransformKind.UNMAPPABLE.value:
        offending = [param for param in _FORBIDDEN_ON_UNMAPPABLE if supplied[param] is not None]
        if offending:
            raise ValueError(f"An UNMAPPABLE transformation declares that no point of one space corresponds to a point of the other, so it carries no map: drop {', '.join(offending)}, or use a kind that does map.")

    params = _assemble_edge_params(
        kind=kind,
        noun="transformation",
        scale=scale,
        translation=translation,
        affine=affine,
        input_axes=input_axes,
        output_axes=output_axes,
        reason=reason,
    )

    # The field itself, for the one kind whose map is an array rather than a formula. The
    # caller always states it -- an edge whose map is implicit is an edge nobody can read --
    # but a *self* field is stored as null: see `Transformation.field`, where PROTECT would
    # otherwise make a dereferenced mask undeletable.
    if kind == enums.TransformKind.FIELD.value:
        if field is None:
            raise ValueError("A FIELD transformation's map is the values of an array, so it requires `field`: the coordinate system of that array. Pass the input's own system when the array's pixels are themselves the map, as for a label mask.")
        assert_field_is_dereferenceable(field)
        assert_field_produces(field=field, output_axes=output_axes or [])
        if field.pk == input_system.pk:
            field = None
    elif field is not None:
        raise ValueError(f"A {kind} transformation's map is in its parameters, not in an array, so it takes no `field`")

    assert_edge_rank(
        kind=kind,
        params=params,
        input_axes=input_axes,
        output_axes=output_axes,
        input_system=input_system,
        output_system=output_system,
    )
    # Orthogonal to kind and to rank -- *where* the map applies rather than what it is -- so it
    # is checked beside them rather than inside the per-kind branches.
    assert_selector(selector, input_system)

    # No collision guard, and no registration/derivation split (RFC-9). A space may hold
    # rival edges about one dataset and every one of them is exposed; which route a placement
    # takes is settled by the widest-path search in :func:`_bfs_tree`, not by refusing the
    # second edge at write time. `value_relation` likewise rides whichever edge its author
    # thinks it describes -- there is no longer a class of edge across which values are known
    # not to travel, because there is no longer a class of edge.
    validity = validity.value if hasattr(validity, "value") else validity
    value_relation = value_relation.value if hasattr(value_relation, "value") else value_relation
    keeps_axes = kind in _AXIS_KINDS
    edge = models.Transformation.objects.create(
        kind=kind,
        name=name,
        input=input_system,
        output=output_system,
        input_axes=input_axes if keeps_axes else None,
        output_axes=output_axes if keeps_axes else None,
        params=params,
        field=field,
        validity=validity or enums.PlacementValidityChoices.MANUAL.value,
        value_relation=value_relation,
        selector=selector,
        creator=ctx.user,
        organization=ctx.organization,
    )
    _project_by_dimension_children(edge, ctx)
    return edge


def _project_by_dimension_children(edge: "models.Transformation", ctx: CreationContext) -> None:
    """Write a BY_DIMENSION's own parameters out as child rows as well as into ``params``.

    **The problem this solves is that the numbers were unreadable.** A BY_DIMENSION carrying an
    ``affine`` used to be one childless row: the matrix went into ``params``, was composed
    server-side by `_sub_matrix`, and reached no client at all -- `ByDimensionTransformation`
    publishes ``transformations`` and no ``affine``/``scale``/``translation`` field, unlike
    `AffineTransformation`, which resolves the very same ``params['affine']``. Every consumer
    that composes a path client-side folds over that empty child list and gets an identity back:
    the viewer's own `transformGraph.ts` (placement) and `axisPath.ts` (attribute probes) both do.
    So a rotated, scaled lattice registered into an image rendered unrotated and unscaled at the
    origin, and nothing anywhere reported an error. Filed as item 9 of MIKRO_BACKEND_PROPOSALS.md
    and measured against the Visium HD ingest, where a 412x400 bin lattice that should span
    24 073 x 23 372 px was drawn 412 x 400 px -- 58x too small.

    **``params`` stays the only source of truth; these rows are a projection of it.** `_sub_matrix`
    reads a params-carried map *in preference to* children precisely so that this cannot become a
    second answer -- a refinement through `updateTransformation` rewrites ``params``, and the
    server's composition follows it whether or not anything remembered to re-project. What the
    projection buys is a client that can read the map at all, so it is regenerated on every params
    write rather than left to drift.

    **The split follows `_params_matrix` exactly, including its precedence.** An ``affine``
    silently wins over ``scale``/``translation`` there, so it wins here too -- projecting all three
    would publish a map the server never had. Otherwise scale comes before translation, the order
    that function applies them in and the order `_sequence` already writes them in.

    **A BY_DIMENSION with no map of its own is left alone entirely.** `world_edge` writes one with
    ``params={}`` and a single IDENTITY child, and there the child is not a projection but the map
    itself -- deleting it would erase the edge's whole content.

    Children omit their endpoints and their axis lists, exactly as a SEQUENCE's do: the wrapper
    supplies both, and a child answers to its parent's named subset (see `_rank_endpoints`).
    """
    if edge.kind != enums.TransformKind.BY_DIMENSION.value:
        return

    params = edge.params or {}
    if "affine" in params:
        projected = [(enums.TransformKindChoices.AFFINE.value, {"affine": params["affine"]})]
    else:
        projected = [
            (child_kind, {name: params[name]})
            for child_kind, name in (
                (enums.TransformKindChoices.SCALE.value, "scale"),
                (enums.TransformKindChoices.TRANSLATION.value, "translation"),
            )
            if name in params
        ]

    if not projected:
        return

    # Regenerated rather than reconciled: these rows carry no identity of their own -- no name,
    # no endpoints, nothing an author chose -- so there is nothing in them worth preserving
    # across a refinement, and a diff would be more code with more ways to be wrong.
    edge.children.all().delete()
    for order, (child_kind, child_params) in enumerate(projected):
        models.Transformation.objects.create(
            kind=child_kind,
            parent=edge,
            order=order,
            params=child_params,
            creator=ctx.user,
            organization=ctx.organization,
        )


def derivation_edges(dataset: "models.ArrayDataset") -> list["models.Transformation"]:
    """The edges placing a derived dataset's pixels in the spaces they were derived from.

    A derived dataset -- a deconvolution, a segmentation, a projection, a resample -- is not
    spatially free-floating: its pixels stand in a definite relation to the lenses they were
    computed from, and each such relation is an edge like any other (IDENTITY for an in-place
    op, TRANSLATION for a crop, SCALE for a resample, BY_DIMENSION for a projection that
    drops an axis). Recording them as attributes instead would be a second copy of spatial
    facts that no spatial query could walk.

    They are the edges out of the dataset's intrinsic system that land in *another
    container*. An edge into a scene's world is a registration, not a derivation: a
    registration says where the data was put, a derivation says where it came from.

    **Another container, not another dataset.** The keep-rule used to resolve the output to
    an ``ArrayDataset``, which meant a parent that was a table or a mesh collection resolved to
    ``None`` -- or, worse, to the *table's own* source image one hop further on -- and the
    edge was dropped. A derivation from a table then read back as no parent at all. The rule
    is container identity now (:func:`is_derivation_edge`), which is the same question asked
    of a kind of thing that can actually answer it.

    **The order is the priority, and the first edge is the primary parent.** A fusion of
    two acquisitions has two real parents, but lineage needs a rule for which one is
    primary -- and that rule is the creator's declared order, written at ingest as pk
    order, not an accident of a `.first()`. The primary is the fact tree's one parent
    edge: it alone places (RFC-6); the rest are recorded facts that ``derivedFrom``
    reports and no placement walk crosses.

    **Kind-blind, and it must stay that way.** An UNMAPPABLE derivation is still a
    derivation -- "this came from that, and the geometry did not survive" is the fact it
    exists to record, and it is the only machine-readable answer to "why can this not be
    placed". The *walks* refuse that edge (see :func:`is_traversable`); this, which merely
    reports it, does not. Filter it here and ``derivedFrom`` omits it, which is the
    silence the kind was invented to break. (Creation refuses an UNMAPPABLE entry ahead of
    a mappable one, so a kind-blind first element is still the placing parent.)
    """
    intrinsic = dataset.intrinsic_coordinate_system
    if intrinsic is None:
        return []

    candidates = list(models.Transformation.objects.filter(input=intrinsic, parent__isnull=True).select_related("output").order_by("pk"))
    # One batched key lookup for every candidate's output, rather than a `system_dataset`
    # per edge -- which was a handful of queries each and grew with the dataset's fan-out.
    keys = container_map({edge.output_id for edge in candidates if edge.output_id})
    return [edge for edge in candidates if is_derivation_edge(edge, of_container=("dataset", dataset.pk), keys=keys)]


def _datasets_derived_into(output_filter: "Q", exclude_pk: int) -> list["models.ArrayDataset"]:
    """The datasets whose derivation edges land in the systems `output_filter` selects.

    The other end of :func:`derivation_edges`, read from the source's side: a derivation
    edge's input is the derived dataset's intrinsic system, so an edge landing here names
    a child. Requiring the input to *be* an intrinsic system is what keeps this to datasets:
    a mesh collection or a table dataset derives from data the same way, but its edge starts
    at the collection's own system and it is not an ArrayDataset.

    Kind-blind and priority-blind, exactly as the forward is. An UNMAPPABLE child still came
    from here, and so did a fusion that named this source second -- both are facts this
    reports; neither is a path any placement walk crosses.
    """
    edges = (
        models.Transformation.objects.filter(output_filter, parent__isnull=True, input__datasets__isnull=False)
        .prefetch_related("input__datasets")
        .order_by("pk")
    )

    seen: set[int] = {exclude_pk}
    derived: list[models.ArrayDataset] = []
    for edge in edges:
        # A child fused from two lenses of one source has two edges into it, and is one child.
        child = next(iter(edge.input.datasets.all()[:1]), None)
        if child is None or child.pk in seen:
            continue
        seen.add(child.pk)
        derived.append(child)
    return derived


def derived_datasets(dataset: "models.ArrayDataset") -> list["models.ArrayDataset"]:
    """The datasets computed from this one: every dataset whose `derivedFrom` names a space of ours."""
    return _datasets_derived_into(
        Q(output__datasets=dataset) | Q(output__lenses__dataset=dataset) | Q(output__data_arrays__dataset=dataset),
        exclude_pk=dataset.pk,
    )


def derived_containers(dataset: "models.ArrayDataset") -> list:
    """Everything computed from this dataset, whatever kind of container it is.

    The wider sibling of :func:`derived_datasets`, which stays honestly narrow: its walk
    requires the edge's input to *be* an intrinsic system, which is exactly what confines it
    to array datasets. That was the whole answer while only a dataset could be derived from
    anything; now a measurement table, a mesh collection or an annotation collection can
    name this dataset too, and a field called `derivedDatasets` returning a table would be a
    field whose name lies. So this is a second field rather than a widening of that one.

    Kind-blind and priority-blind, as the narrow one is: an UNMAPPABLE child still came from
    here, and a fusion that named this source second is still a child.
    """
    spaces = Q(output__datasets=dataset) | Q(output__lenses__dataset=dataset) | Q(output__data_arrays__dataset=dataset)
    edges = list(models.Transformation.objects.filter(spaces, parent__isnull=True).select_related("input", "output").order_by("pk"))
    if not edges:
        return []

    keys = _keys_for(edges)

    # One container may have several edges into this dataset -- a fusion of two of its
    # lenses -- and is still one child; pk order makes the answer the creators' order.
    seen: set[tuple] = {("dataset", dataset.pk)}
    wanted: list[tuple] = []
    for edge in edges:
        key = keys.get(edge.input_id) if edge.input_id else None
        if key is None or key in seen or not is_derivation_edge(edge, of_container=key, keys=keys):
            continue
        seen.add(key)
        wanted.append(key)

    by_kind: dict[str, list[int]] = {}
    for kind, pk in wanted:
        by_kind.setdefault(kind, []).append(pk)

    found: dict[tuple, object] = {}
    for label, pks in by_kind.items():
        found.update({(label, row.pk): row for row in MODEL_BY_KEY[label].objects.filter(pk__in=pks)})
    return [found[key] for key in wanted if key in found]


def lens_derived_datasets(lens: "models.Lens") -> list["models.ArrayDataset"]:
    """The datasets computed from this lens' selection.

    An unsliced lens owns no system -- its space *is* the dataset's intrinsic -- so it
    reports what was derived from the whole grid, and two unsliced lenses over one dataset
    report the same children. That is not a leak: the model has one space there, and
    saying otherwise would invent a distinction nothing stored can support.
    """
    space = lens.space
    if space is None:
        return []
    return _datasets_derived_into(Q(output=space), exclude_pk=lens.dataset_id)


def primary_derivation_edge(dataset: "models.ArrayDataset") -> "models.Transformation | None":
    """The derivation edge that places a derived dataset: the first, by its creator's declared order."""
    edges = derivation_edges(dataset)
    return edges[0] if edges else None


def lineage_ancestors(dataset: "models.ArrayDataset") -> list["models.ArrayDataset"]:
    """The primary-parent chain above a dataset, nearest first. Empty for a root dataset.

    The primary parent only, at every hop: placement walks the fact *tree* (RFC-6), and
    the tree's one parent edge for a derived dataset is its primary derivation. A fusion
    owes its other parents historically -- ``derivation_edges`` reports every one -- but
    it sits where its primary sits, full stop; wanting it placed some other way is a
    re-anchoring (reorder ``derivedFrom`` at ingest, or register the fusion's own system),
    never a path the walk finds on its own.

    This is the *spatial* lineage -- who places whom -- so it stops at an UNMAPPABLE
    primary. Data whose geometry did not survive inherits nothing from that source: it
    has its own pixel grid and nothing relates the two, however much it owes the source
    historically. (``derivation_edges`` still reports that edge; the historical lineage is
    intact. It is only placement that ends there.)
    """
    ancestors: list[models.ArrayDataset] = []
    seen: set[int] = {dataset.pk}
    current = dataset

    while True:
        edge = primary_derivation_edge(current)
        if edge is None or edge.output is None or not is_traversable(edge):
            return ancestors
        source = system_dataset(edge.output)
        if source is None or source.pk in seen:
            return ancestors  # A cycle is nonsense, but it must not hang the request.
        seen.add(source.pk)
        ancestors.append(source)
        current = source


def primary_lineage_root(dataset: "models.ArrayDataset") -> "models.ArrayDataset":
    """The dataset at the top of the primary-parent chain -- the one whose placement carries the rest.

    A fusion has several parents, but "where does this data ultimately come from" must
    name exactly one dataset, and the creator's declared order says which: the first
    entry, at every hop. Registering the root places everything derived from it, which
    is why a client aligning a lineage authors its edge against the root rather than
    against each descendant.
    """
    seen: set[int] = {dataset.pk}
    current = dataset

    while True:
        edge = primary_derivation_edge(current)
        if edge is None or edge.output is None or not is_traversable(edge):
            return current
        source = system_dataset(edge.output)
        if source is None or source.pk in seen:
            return current  # A cycle is nonsense, but it must not hang the request.
        seen.add(source.pk)
        current = source


def _placement_universe(
    space: "models.CoordinateSystem | None", source_system: "models.CoordinateSystem"
) -> tuple["models.CoordinateSystem", list[int], list["models.Transformation"]]:
    """The flat edge universe a single-source placement question searches.

    Fetched in one query: the source system's own edges, every edge owned by a dataset in
    its lineage, and the destination space's edges -- which include its registrations,
    because those are a property of the *space*, not of any scene's say-so. Takes a space
    rather than a scene for exactly that reason: nothing here reads a composition, so
    asking for one would be asking the caller to prove more than the question needs.
    Shared verbatim by :func:`is_placeable_in` (which walks it) and
    :func:`assert_placeable_in` (which classifies a failure), so the two never disagree
    about which edges the walk was allowed to see. Returns ``(space, lineage_ids, edges)``.
    """
    world = space

    dataset = system_dataset(source_system)
    lineage_ids = [dataset.pk] + [ancestor.pk for ancestor in lineage_ancestors(dataset)] if dataset else []

    edges = list(
        models.Transformation.objects.filter(parent__isnull=True)
        .filter(
            # The source system itself: a collection-owned (mesh collection / table
            # dataset) system belongs to no dataset, so its derivation edge would not
            # enter through the lineage terms below.
            Q(input=source_system)
            | Q(input__datasets__in=lineage_ids)
            | Q(input__lenses__dataset__in=lineage_ids)
            | Q(input__data_arrays__dataset__in=lineage_ids)
            | Q(input=world)
            | Q(output=world)
        )
        .distinct()
        .select_related("input", "output")
        .prefetch_related("children", "input__axes", "output__axes")
    )
    return world, lineage_ids, edges


def _container_key(system: "models.CoordinateSystem | None") -> tuple | None:
    """The container a system belongs to, off preloaded FKs: the node of the fact tree it lives under."""
    if system is None:
        return None
    return container_map([system.pk]).get(system.pk)


def container_map(system_ids: "Iterable[int]") -> dict[int, tuple]:
    """``{coordinate_system_id: container key}`` for a set of spaces, one query per container.

    The batched form of :func:`_container_key`, and the successor to
    :func:`residence_map` for every question of the form *"whose fact tree is this space
    in?"*. Three differences from the dataset-keyed map it replaces, all of them the point:

    **A collection keys to itself.** ``residence_map`` knew only about datasets, so a mesh
    or table system was simply absent from it and every caller had to special-case the
    hole. Here it is ``("tabledataset", pk)`` -- a node of the fact tree in its own right,
    which is what makes a table nameable as a parent at all.

    **No edge is followed.** ``system_dataset`` resolves a collection's space by walking its
    derivation edge one more hop, which answers *"which dataset's bucket does this search
    belong to"* -- a different and still-useful question. Asked for *identity*, that hop is
    exactly the bug: a table parent resolves to the table's own source image, so a child
    derived from the table looks derived from the grandparent.

    **A resident-less space keys to itself**, ``("system", pk)``. A world belongs to no
    container, and that is what :func:`is_derivation_edge` reads to tell a registration from
    a lineage.

    Iterated in ``_KEYING_ORDER`` so a space its dataset itself lives in keys to the
    dataset rather than to the dataset's level or lens that also sit there.
    """
    ids = list(system_ids)
    if not ids:
        return {}

    mapping: dict[int, tuple] = {}
    for container in _KEYING_ORDER:
        # Descending pk so the lowest wins each slot, matching `residence_map`'s rule: this
        # exists to scope a search, and any resident anchors the same scope.
        for system_id, root_id in container.model.objects.filter(coordinate_system_id__in=ids).order_by("-pk").values_list("coordinate_system_id", container.root_field):
            mapping[system_id] = (container.key, root_id)

    return {system_id: mapping.get(system_id, ("system", system_id)) for system_id in ids}


def container_q(keys: "Iterable[tuple]", *, field: str) -> Q:
    """A ``Q`` matching edges whose ``field`` system belongs to one of these containers.

    The database-side mirror of :func:`container_map`, and the reason a container key is a
    ``(kind, pk)`` pair rather than an opaque token: the set has to be turned back into a
    join. A dataset key matches its own grid *and* its lenses' and levels', because those
    are the dataset's own systems; a collection key matches the one system it owns.

    A ``("system", ...)`` key matches nothing. A resident-less space is not a container, so
    there is no tree hanging under it to fetch -- the world's own edges come in through the
    explicit ``Q(input=world) | Q(output=world)`` at the call site.
    """
    by_kind: dict[str, set[int]] = {}
    for kind, pk in keys:
        by_kind.setdefault(kind, set()).add(pk)

    query = Q(pk__in=())
    for container in CONTAINERS:
        pks = by_kind.get(container.key)
        if not pks:
            continue
        # A dataset key matches its own grid *and* its levels' and lenses': three containers
        # share that key, so this loop contributes three joins for it.
        lookup = f"{field}__{container.related_name}__in" if container.root_field == "pk" else f"{field}__{container.related_name}__dataset__in"
        query |= Q(**{lookup: pks})
    return query


def is_derivation_edge(edge: "models.Transformation", *, of_container: tuple | None, keys: dict[int, tuple]) -> bool:
    """Whether this edge records *where its input came from*, rather than where it was put.

    **Both halves are load-bearing.** An edge is a derivation when its output lands in a
    container at all, *and* when that container is a different one:

    - **Lands in a container.** A registration points into a shared space -- a world, an
      atlas -- and a world has no residents, so its key is ``("system", ...)``. Dropping
      this half reports every registration as a parent, which is precisely the confusion
      ``_derivation_target`` warns about: *"an edge into a shared space leaves the dataset
      too, but it lands nowhere."*
    - **A different container.** A lens edge, a level edge and a physical-space edge all
      leave their system and stay inside their own dataset; none of them is a derivation.

    This replaces the older test of "does the output resolve to a different *ArrayDataset*",
    which silently dropped an edge whose parent was a table or a mesh collection -- the
    parent vanished from ``derivedFrom`` rather than erroring.

    **A FIELD is never a derivation**, and this is the one kind that is excluded. It records
    that a label mask's pixels *are* the map into a table of objects -- a lookup, not a
    statement about where either side came from. RFC-7 already draws that line for the
    attribute-plan walk ("FIELD edges are payload, never connectivity"), and it has to be
    drawn here too: a FIELD points mask -> table, so left in, a mask with a dereference
    would report the table as the thing it was *derived from*. The table's real provenance
    is its own ``derivedFrom``, which is a separate edge saying a separate thing.

    Under the dataset-keyed predicate this was hidden by accident rather than by rule: the
    output resolved through the table's own derivation edge back to the mask, compared equal
    and was dropped. Container keys removed the accident, which is what surfaced the rule.
    """
    if edge.output_id is None or edge.kind == enums.TransformKindChoices.FIELD.value:
        return False
    target = keys.get(edge.output_id)
    if target is None or target[0] == "system":
        return False
    return target != of_container


def reachable_in(adjacency: dict[int, list[tuple["models.Transformation", bool, int]]], start: int) -> set[int]:
    """Every node the walk reaches from ``start``, including ``start`` itself.

    The bare traversal, shared by the two questions that ask it of different universes:
    :func:`_fact_reachable` over a flat edge list, and
    :meth:`core.logic.scene_graph.SceneGraph.placement_state` over the request-scoped
    universe. They must agree about what "reaches nothing" means -- it is what separates a
    missing registration from a stated impossibility -- and two copies of a BFS are two
    chances to disagree.
    """
    reached = {start}
    frontier = [start]
    while frontier:
        next_frontier: list[int] = []
        for node in frontier:
            for _edge, _inverted, neighbor in adjacency.get(node, ()):
                if neighbor not in reached:
                    reached.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return reached


def _fact_reachable(source_system: "models.CoordinateSystem", edges: list["models.Transformation"], *, require_affine: bool = False) -> set[int]:
    """Every system the source reaches, across every traversable edge.

    No fact/claim filter any more (RFC-9): an edge is an edge, and how far to trust one is
    read off its own ``validity``.

    **Selector-scoped edges count.** This walk answers existence -- can this data be placed --
    and a dataset registered per channel is registered. Gating them on a coordinate nobody
    supplied made a per-channel registration invisible here, so `createLayer` refused the very
    layer the feature exists to allow, and an unplaced layer was told nothing was registered
    when several things were. Nothing composes this walk's result into a map; the callers that
    do compose (`placement_path`, `condensed_placement`) take their own `at` and cross a scoped
    edge only when it is fixed.

    ``require_affine`` asks the narrower question the layer gate needs: not "does this data
    reach the space" but "does it reach it across steps that compose into a matrix". Threaded
    rather than always-on because the two callers that classify a *failure* -- the UNREGISTERED
    / UNMAPPABLE split -- want the wide answer, and a narrow one would make them call an
    unregistered thing unmappable.
    """
    return reachable_in(adjacency_of(edges, admit_scoped=True, require_affine=require_affine), source_system.pk)


def is_placeable_in(space: "models.CoordinateSystem | None", source_system: "models.CoordinateSystem | None", *, require_affine: bool = False) -> bool:
    """Whether a registration into this space that places this source exists.

    True when one of the space's registrations anchors a space the source fact-reaches, or
    when the source fact-reaches the space outright (it *is* that space). An existence
    check, not a choice: this asks whether the graph already holds the claim, and says
    nothing about which of several routes a walk would take if there are rivals (RFC-9).
    The boolean core of :func:`assert_placeable_in`, and the single source of truth the
    ``placeableIn`` filter shares with creation-time refusal. A sourceless layer is never
    placeable.

    **A per-index registration is a registration.** Both halves below count selector-scoped
    edges: the walk through :func:`_fact_reachable`, and the direct-edge check, which never
    consulted the selector at all. That agreement used to be an accident of the second branch
    reading only ``is_traversable``, and it made the answer depend on the shape of the route --
    a dataset whose per-channel correction reached world in one hop was placeable, the same
    dataset reaching it in two was not. Existence does not depend on where the asker stands;
    only the map does, and that is `placement_path`'s question, with its own ``at``.

    **``require_affine`` narrows it to a placement that can actually be drawn**: every forward
    step must have a closed affine form, so the route the graph holds is one `condense_path`
    will compose rather than refuse. Both halves below take it, and they must -- reading it in
    the walk and not in the direct-edge check would make the answer depend on the shape of the
    route, a FIELD registration reaching world in one hop passing where the same registration
    two hops out did not. The same reasoning the scoped-edge paragraph above records, for the
    same reason: existence does not depend on where the asker stands.
    """
    if source_system is None:
        return False
    world, _lineage_ids, edges = _placement_universe(space, source_system)
    reachable = _fact_reachable(source_system, edges, require_affine=require_affine)
    if world.pk in reachable:
        return True
    return any(
        edge.output_id == world.pk
        and is_traversable(edge)
        and (not require_affine or is_condensable(edge))
        and edge.input_id in reachable
        for edge in edges
    )


def assert_placeable_in(
    space: "models.CoordinateSystem | None",
    source_system: "models.CoordinateSystem | None",
    *,
    destination: str | None = None,
) -> None:
    """Reject a source nothing places in this space.

    Creating a layer in a scene *is* a claim that the data belongs in that scene's world,
    and the graph must already hold that claim: a registration is authored exactly once,
    explicitly (``createTransformation`` into the space), never fabricated as a side effect
    of a layer mutation. The check itself knows nothing about scenes -- it is a question
    about a space and a source -- so ``destination`` is how a caller that *does* have a
    composition in hand names it in the error ("the world of scene 'Foo'"); it defaults to
    the space's own name.

    **What is claimed is stronger than reachability.** The route the graph holds must be one
    that composes into a matrix -- ``require_affine`` -- because a layer is a thing a renderer
    draws, and it draws it with an affine. A FIELD relates two spaces by the values of an
    array and has no closed form, so a path across one is a path `condense_path` refuses:
    accepting such a layer meant `asAffine` raised for a layer creation had promised was
    placed. Reachability is not placement.

    A source is placed or it is not, and when it is not the error says which of the three very
    different gaps it is:

    **Unregistered** -- placeable, but nobody has authored the registration yet. The
    error points at the mutation that closes the gap.

    **Unmappable** -- the source's data reaches other spaces only across an UNMAPPABLE
    relation, which declares that no point correspondence exists. There is no missing
    registration to author, and the error says so instead of sending someone to look
    for one.

    **Not affinely placed** -- registered, reachable, and still not drawable: every route runs
    through a step with no closed form. Its own message because both others are actively
    *wrong* here -- "author the registration" sends someone after an edge that already exists,
    and "unmappable" denies a correspondence the FIELD asserts.

    The first two mirror :meth:`core.logic.scene_graph.SceneGraph.placement_state` so that
    creation-time refusal and query-time state never disagree. The third has no twin there on
    purpose: `placement_state` answers *is this placed*, and such a layer is -- `pathToWorld`
    returns its route and `placementInvariance` reads DIFFEOMORPHIC. Whether that placement
    condenses is `asAffine`'s question, and it is already answered there.

    Flat cost, deliberately: one edge query over the source's lineage universe plus the
    space's edges -- never a scene's layers, which is the other half of why this takes a
    space -- so creating a layer does not get slower with every layer already in the scene.
    Flat in the *layers*, which is the growth that matters; the refusal path re-fetches that
    universe to classify which gap it is, and pays for a verdict nobody reads twice.
    """
    if source_system is None:
        raise ValueError("The layer's data has no coordinate system, so it has no space to be placed by. Nothing sourceless can be composed into a scene.")

    if is_placeable_in(space, source_system, require_affine=True):
        return

    where = destination or (f"space '{space.name}'" if space is not None else "the destination space")
    _space, lineage_ids, edges = _placement_universe(space, source_system)

    # Asked before either gap, because it is not a gap: the wide question passing while the
    # narrow one failed says the registration is *there* and simply has no matrix in it. Both
    # messages below would send the reader somewhere there is nothing to do.
    if is_placeable_in(space, source_system):
        # Named from the *affine* reachable set, not from the whole universe: an edge the
        # source cannot even get to is not what stopped it, and listing one sends the reader
        # to fix a registration that was never on the route.
        reached = _fact_reachable(source_system, edges, require_affine=True)
        blocking = sorted(
            {f"{edge.pk} ({edge.kind})" for edge in edges if edge.input_id in reached and is_traversable(edge) and not is_condensable(edge)}
        )
        raise ValueError(
            f"'{source_system.name}' is registered into {where}, but nothing places it there *affinely*: "
            f"every route runs through a step with no closed form{' -- ' + ', '.join(blocking) if blocking else ''}. "
            "A FIELD gives its map as the values of an array rather than as a matrix, and a step that changes "
            "rank without being a BY_DIMENSION states no square map, so neither composes into the affine a "
            f"layer is drawn with. Author a registration into {where} that does -- then create the layer."
        )

    if _blocked_by_unmappable(source_system, set(lineage_ids), edges):
        raise ValueError(
            f"'{source_system.name}' can not be placed in {where}: "
            "its data reaches other spaces only across an UNMAPPABLE relation, which declares that "
            "no point correspondence exists. There is no missing registration to author here."
        )
    raise ValueError(
        f"Nothing places '{source_system.name}' in {where}. "
        "Author the registration -- createTransformation from the data's system (or any system it "
        f"reaches through its own facts) into {where} -- then create the layer."
    )


def _blocked_by_unmappable(
    source_system: "models.CoordinateSystem",
    lineage_ids: set[int],
    edges: list["models.Transformation"],
) -> bool:
    """Whether an unplaced source is unplaced because of a stated non-correspondence.

    Mirrors :meth:`~core.logic.scene_graph.SceneGraph.placement_state` against the flat
    universe already fetched, and asks its two halves in order:

    **Does the source reach anywhere at all?** If a traversable edge takes it to any other
    space, then a registration authored from *there* would place it, and the honest answer
    is that one is missing. This half is what makes the verdict a statement rather than a
    guess. Asking only the second half -- is any lineage edge UNMAPPABLE -- called a fusion
    with one unmappable parent impossible, though registering its other parent places it,
    and told whoever hit it not to bother looking. A collection was read even more coarsely,
    off its *earliest* edge alone, so a later traversable derivation was never consulted.

    **Then: is there a stated non-correspondence to blame?** Reaching nowhere is ordinary
    for freshly ingested data, and that is UNREGISTERED. It is UNMAPPABLE only when
    something on the source's own lineage says why -- an edge recording that the operation
    which produced this data left no point correspondence behind.

    What the verdict claims is therefore exact: the graph holds no route and holds a reason.
    It does not claim nobody could ever author an edge out of this system by hand -- anyone
    can write a number -- only that the data's own history says such a number would not mean
    anything, which is the thing worth putting in front of a person.
    """
    # The reachability half. `_fact_reachable` starts at the source, so reaching only
    # itself is reaching nowhere.
    if _fact_reachable(source_system, edges) != {source_system.pk}:
        return False

    def owner_dataset_id(system: "models.CoordinateSystem | None") -> int | None:
        if system is None:
            return None
        return residence_map([system.pk]).get(system.pk) if system is not None else None

    # The reason half. A collection owns its system outright, so an edge leaving that system
    # is its own; a dataset's are found through its lineage.
    return any(not is_traversable(edge) and (edge.input_id == source_system.pk or owner_dataset_id(edge.input) in lineage_ids) for edge in edges if edge.input_id)


def residents_exist(system: "models.CoordinateSystem") -> bool:
    """Whether any data lives in this space.

    The residence-model successor to ``kind != SHARED``: a space nothing lives in is a pure
    reference frame, and that is the only distinction the old four-value label was carrying.
    """
    return any(getattr(system, relation).exists() for relation in RESIDENT_RELATIONS)


def collection_in(system: "models.CoordinateSystem"):
    """The mesh / table / annotation collection living in this space, if one does.

    Only the seeding and keying paths ask, and they ask once per space rather than once per
    edge, so three bounded reads are the right shape here. A caller with a *set* of spaces
    wants :func:`container_map` instead.
    """
    for container in COLLECTION_CONTAINERS:
        found = next(iter(getattr(system, container.related_name).all()[:1]), None)
        if found is not None:
            return found
    return None


def residence_map(system_ids: "Iterable[int]") -> dict[int, int]:
    """``{coordinate_system_id: dataset_id}`` for a set of spaces, in three batched queries.

    **The direction is the whole point.** Ownership asked *space -> data*, which was a local
    column and became a reverse lookup the moment the key moved. Residence asks *data ->
    space*, and that is a local column on the data row -- so this reads `coordinate_system_id`
    off the datasets, levels and lenses and never traverses back from a space. Three ``IN``
    queries for any number of spaces, flat in the spaces *and* in the residents.

    A space several datasets live in (a hundred tiles on one stage) maps to the lowest pk,
    deterministically: this exists to *scope a search*, and any resident anchors the same
    scope. Where the whole set matters, ask the space for its residents.
    """
    ids = list(system_ids)
    if not ids:
        return {}

    mapping: dict[int, int] = {}
    # Descending pk so the lowest wins each slot, and datasets last: a space a dataset itself
    # lives in is that dataset's, even when another's lens or level also sits there.
    for source, dataset_field in ((models.Lens, "dataset_id"), (models.DataArray, "dataset_id"), (models.ArrayDataset, "pk")):
        for system_id, dataset_id in source.objects.filter(coordinate_system_id__in=ids).order_by("-pk").values_list("coordinate_system_id", dataset_field):
            mapping[system_id] = dataset_id
    return mapping


def _fk_dataset_id(system: "models.CoordinateSystem | None") -> int | None:
    """The dataset a system belongs to by its ownership FK -- no derivation-edge follow.

    The FK-only sibling of :func:`system_dataset`: a collection's own (mesh / table) system
    is anchored to a dataset by an *edge*, not an FK, so this returns None for it. That is
    the wanted behaviour for the lens key -- a lens always lives on a dataset-owned system --
    and it keeps the batch a pure in-memory read over the already-fetched FKs.
    """
    if system is None:
        return None
    return residence_map([system.pk]).get(system.pk)


def _keys_for(edges: "Iterable[models.Transformation]") -> dict[int, tuple]:
    """The container key of every endpoint of these edges, in one batch."""
    endpoints = {edge.input_id for edge in edges if edge.input_id} | {edge.output_id for edge in edges if edge.output_id}
    return container_map(endpoints)


def _derivation_descendants(container_keys: set[tuple]) -> set[tuple]:
    """Every container the fact tree hangs below one of these -- primary derivations only.

    The dual of :func:`lineage_ancestors`, which walks the primary chain *up* from a
    candidate; this walks it *down* from a registered source to everything it places. A
    child descends from its **primary** parent only: a fusion whose primary is elsewhere
    is not placed by its secondary, however real that edge is as history. And it stops at
    an UNMAPPABLE primary for the same reason lineage does: a derivation whose geometry
    did not survive places nothing downstream.

    **Containers, not datasets.** Keyed on dataset ids this walk could not see a collection
    at either end -- `_fk_dataset_id` was None for one -- so a table-parented dataset was
    never discovered as a descendant and was placed by nothing. A table registered into a
    world now carries its reconstruction along, which is the whole point of a localization
    table having a metric space.

    Two bounded queries per generation: one finds the candidate children (any derivation
    edge landing in the frontier), one fetches the candidates' own derivation edges so the
    primary -- the first cross-container edge by the creator's declared (pk) order, exactly
    :func:`primary_derivation_edge`'s rule -- is decided in memory, never per child.
    """
    descendants: set[tuple] = set()
    frontier = set(container_keys)
    seen = set(container_keys)

    while frontier:
        edges = list(models.Transformation.objects.filter(parent__isnull=True).filter(container_q(frontier, field="output")).select_related("input", "output"))
        keys = _keys_for(edges)

        candidates: set[tuple] = set()
        for edge in edges:
            child = keys.get(edge.input_id) if edge.input_id else None
            # A same-container edge (a lens, level or physical-space edge) is not a
            # derivation; only a cross-container one carries placement downstream.
            if child is None or child[0] == "system" or child in seen:
                continue
            if not is_derivation_edge(edge, of_container=child, keys=keys):
                continue
            candidates.add(child)

        next_frontier: set[tuple] = set()
        if candidates:
            out_edges = list(
                models.Transformation.objects.filter(parent__isnull=True).filter(container_q(candidates, field="input")).select_related("input", "output").order_by("pk")
            )
            out_keys = _keys_for(out_edges)
            primary: dict[tuple, models.Transformation] = {}
            for edge in out_edges:
                child = out_keys.get(edge.input_id) if edge.input_id else None
                if child is None or not is_derivation_edge(edge, of_container=child, keys=out_keys):
                    continue
                primary.setdefault(child, edge)
            for child, edge in primary.items():
                if child in seen or not is_traversable(edge):
                    continue
                if out_keys.get(edge.output_id) in seen:
                    seen.add(child)
                    descendants.add(child)
                    next_frontier.add(child)
        frontier = next_frontier

    return descendants


def _placement_seeds(space: "models.CoordinateSystem") -> set[tuple]:
    """The containers this space places *directly* -- with no lineage in between.

    The registrations' traversable inputs, plus an owned world's own container (and, for a
    collection world, the container it was derived from). Split out of
    :func:`placeable_system_ids_in` because ``derived_only`` needs the two groups told
    apart: everything else placeable in the space hangs below one of these on the
    primary-derivation chain, and a seed is by definition a container that needs no
    lineage tree to be placeable.
    """
    world = space

    registrations = list(
        models.Transformation.objects.filter(parent__isnull=True, output=world)
        # Both endpoints, because `system_dataset` reads each side's residence to bucket the
        # edge; unselected, that reverse walk is a query per registration, which made this grow
        # one query per source in the space.
        .select_related("input", "output")
        .prefetch_related("children", "input__axes", "output__axes")
    )

    # The containers the space's registrations anchor. One batched container map rather than
    # `system_dataset` per registration, which was a query each and made this grow one query
    # per source in the space.
    # Deliberately `is_traversable` and not `is_condensable`, though the walk below narrows to
    # the latter: seeding decides which containers' *edges are fetched*, and dropping a FIELD
    # registration here would hide the fetch of everything affinely upstream of it. The
    # narrowing belongs at the walk, where it is one line and cannot lose a route -- the
    # agreement `tests/test_placeable_in_filter.py` pins is between the walks, not the fetches.
    walkable_inputs = {edge.input_id for edge in registrations if edge.input_id and is_traversable(edge)}
    seeds = {key for key in container_map(walkable_inputs).values() if key[0] != "system"}

    # An owned world (a scene rooted at a dataset's intrinsic pixels, its physical space, or
    # a collection's own space) anchors its own container with no registration at all: the
    # data is in its own space by construction, so the container seeds the set directly.
    world_key = container_map([world.pk]).get(world.pk)
    if world_key is not None and world_key[0] != "system":
        seeds.add(world_key)
        # A collection world *also* seeds what it was derived from, and only across a
        # traversable derivation -- an UNMAPPABLE one places nothing, and seeding through it
        # would make this set disagree with `is_placeable_in`. Kept rather than dropped when
        # the seeding became container-keyed: `_derivation_descendants` closes *downward*,
        # and reaching a collection's source is the one hop that goes the other way.
        if world_key[0] != "dataset":
            derivation = collection_derivation_edge(world)
            if derivation is not None and derivation.output_id is not None and is_traversable(derivation):
                source_key = container_map([derivation.output_id]).get(derivation.output_id)
                if source_key is not None and source_key[0] != "system":
                    seeds.add(source_key)

    return seeds


def placeable_system_ids_in(space: "models.CoordinateSystem", *, derived_only: bool = False) -> set[int]:
    """The ids of every coordinate system with an affinely composable path into this space.

    The batched dual of :func:`is_placeable_in`: rather than ask "can *this* source
    reach the space", it computes the whole set that can, in a bounded fetch and one walk, so a
    filter over thousands of candidates costs a constant number of queries instead of one BFS
    each. It shares the fact-tree rule and the traversal predicates with the single-source
    path, so the two never disagree -- a candidate is in this set exactly when
    ``is_placeable_in(..., require_affine=True)`` says yes (pinned by
    ``tests/test_placeable_in_filter.py``).

    **Strict, with no flag to loosen it.** Every caller of this is a picker -- the
    ``placeableIn`` filters, ``CoordinateSystem.placedSystems`` -- offering candidates a layer
    mutation will then be asked to accept, and a picker that offers what the gate refuses is
    worse than one that offers nothing. The loose question exists on `is_placeable_in` because
    `assert_placeable_in` needs it to *classify a failure*; nothing batched needs to.

    The universe is the space's registrations -- a property of the *space*, shared by every
    scene over it -- closed over the datasets they anchor and those datasets'
    primary-derivation *descendants* (a derived dataset is placed through its primary
    parent's registration), plus the collection edges landing in any anchored dataset (a
    mesh or table reaches world through the image it was extracted from). Reachability is
    then a single reverse walk from world over that universe: every node from which world
    is reachable.

    ``derived_only`` keeps just the systems of the containers that *needed* a lineage tree
    to get here -- the segmentations, deconvolutions and projections riding an ancestor's
    registration -- and drops everything the space registers directly. It narrows, and only
    narrows: the result stays a subset of the full set, so every member is still placeable
    and :func:`assert_placeable_in` needs no twin of it. It is a picker's preference about
    what to offer, never a gate on what may be created.
    """
    world = space

    seeds = _placement_seeds(space)
    # `_derivation_descendants` starts from `seen = set(seeds)` and skips any candidate
    # already in it, so a container the space registers directly can never come back as a
    # descendant of another -- which is exactly what makes a dataset that is *both*
    # registered here and derived from something else here come out of `derived_only`
    # excluded. There is deliberately no `descendants - seeds` below: the subtraction would
    # be a no-op dressed up as the rule.
    descendants = _derivation_descendants(seeds)
    container_keys = seeds | descendants

    # Edges *into* an anchored container as well as *out of* one: the out-edges carry a
    # container's own facts (a dataset's lenses, levels, physical spaces) and its
    # descendants'; the in-edges carry a collection's derivation (a mesh or table system ->
    # the image's intrinsic), the one edge a registered image's own bucket would otherwise miss.
    edges = list(
        models.Transformation.objects.filter(parent__isnull=True)
        .filter(container_q(container_keys, field="input") | container_q(container_keys, field="output") | Q(input=world) | Q(output=world))
        .distinct()
        .select_related("input", "output")
        .prefetch_related("children", "input__axes", "output__axes")
    )

    # The walkable universe: the fact tree, plus this world's own claims. A claim into
    # Every edge is walkable (RFC-9). There is no fact/claim split to filter on, and chains
    # through several spaces are legal -- how far to trust a hop is that edge's `validity`,
    # and which of several routes wins is :func:`_bfs_tree`'s to say, not this fetch's.
    #
    # Selector-scoped edges are walkable here too, and for the same reason `is_placeable_in`
    # counts them: this answers *what can be placed here*, and a dataset corrected per channel
    # can. Excluding them hid such a dataset from the `placeableIn` filter and from the scene
    # builder, so a space that held it offered a scene with the layer missing. No map is
    # composed from this walk -- it yields a set of system ids, not a path.
    adjacency = adjacency_of(edges, admit_scoped=True, require_affine=True)

    # Reverse the adjacency and walk out from world: a node is placeable exactly when world
    # is reachable *from* it, which is world reaching it in the reversed graph.
    reverse: dict[int, list[int]] = {}
    for node, steps in adjacency.items():
        for _edge, _inverted, neighbor in steps:
            reverse.setdefault(neighbor, []).append(node)

    reachable = {world.pk}
    frontier = [world.pk]
    while frontier:
        next_frontier: list[int] = []
        for node in frontier:
            for previous in reverse.get(node, ()):
                if previous not in reachable:
                    reachable.add(previous)
                    next_frontier.append(previous)
        frontier = next_frontier

    if not derived_only:
        return reachable

    # One batched container map over what the walk found, rather than a second walk: which
    # group a system belongs to is a property of its container, and both groups were
    # computed above.
    keys = container_map(reachable)
    return {system_id for system_id in reachable if keys.get(system_id) in descendants}


def _placeable_systems(space: "models.CoordinateSystem", *, derived_only: bool = False) -> list["models.CoordinateSystem"]:
    """The placeable coordinate systems as rows.

    No `select_related` of owners any more: a space has none. Callers that need to know what
    lives in these spaces ask :func:`residence_map` over their ids, in three queries.
    """
    return list(models.CoordinateSystem.objects.filter(pk__in=placeable_system_ids_in(space, derived_only=derived_only)))


def placeable_lens_dataset_ids(space: "models.CoordinateSystem", *, derived_only: bool = False) -> set[int]:
    """The datasets every one of whose lenses is placeable in this space.

    Placeability is a property of the *dataset*, not the individual lens: an unsliced lens'
    space is the intrinsic system, a sliced lens' system reaches it across a crop/scale, and
    a dataset anchored in its physical space reaches the destination through that space -- so
    if any of a dataset's systems reaches it, every lens of it does. Keying on ``dataset_id``
    is therefore both correct and indexed, and needs no ``distinct()``.

    ``derived_only`` passes straight through to :func:`placeable_system_ids_in`: the
    question is asked of the container, and a dataset's lenses and levels share its
    container, so the reduction is the same either way.
    """
    return {dataset_id for system in _placeable_systems(space, derived_only=derived_only) if (dataset_id := _fk_dataset_id(system)) is not None}


def placeable_table_dataset_ids(space: "models.CoordinateSystem") -> set[int]:
    """The table datasets whose own coordinate system is placeable in this space.

    A table owns its system one-to-one, so there is no dataset reduction as there is for a
    lens: the placeable table datasets are exactly those whose system is in the placeable set.
    """
    placeable = {system.pk for system in _placeable_systems(space)}
    return set(models.TableDataset.objects.filter(coordinate_system_id__in=placeable).values_list("pk", flat=True)) if placeable else set()


def categorized_dataset_ids(dataset_ids: "Iterable[int]") -> set[int]:
    """Of these datasets, the ones whose *primary* derivation declares CATEGORIZED.

    The batched twin of :func:`primary_derivation_edge` composed with a ``value_relation``
    test -- the very signal :func:`core.logic.scene._infer_kind` reads to bootstrap a label
    layer, asked of a whole candidate list at once so a picker can offer exactly the masks.
    The reuse is the point: a lens the filter calls a label and a scene builder would draw
    as an image is a disagreement about the data, not about presentation.

    Scoped to ``input__datasets``, matching :func:`derivation_edges`' ``input=dataset.
    intrinsic_coordinate_system`` and deliberately *not* ``container_q(..., field="input")``.
    A dataset's container key also matches its lenses' and levels' systems, so the container
    form would admit an edge authored off one of those -- and since the primary is the first
    by pk over whatever was admitted, the wider set could pick a different primary than
    ``derivation_edges`` does and answer a different question. (``_derivation_descendants``
    keeps the container scope: it asks about placement, which is pinned separately.)

    Two queries: the candidates' out-edges in pk order, then one batched container map over
    their outputs so the cross-container test is decided in memory.
    """
    dataset_ids = set(dataset_ids)
    if not dataset_ids:
        return set()

    edges = list(
        models.Transformation.objects.filter(parent__isnull=True, input__datasets__in=dataset_ids)
        .annotate(_source_dataset=F("input__datasets__id"))
        .select_related("input", "output")
        .order_by("pk")
    )
    keys = container_map({edge.output_id for edge in edges if edge.output_id})

    primary: dict[int, "models.Transformation"] = {}
    for edge in edges:
        dataset_id = edge._source_dataset
        if dataset_id not in dataset_ids or not is_derivation_edge(edge, of_container=("dataset", dataset_id), keys=keys):
            continue
        # First by pk wins, kind-blind: the creator's declared order is the primary parent,
        # exactly `derivation_edges`' rule, and an UNMAPPABLE primary is still the primary.
        primary.setdefault(dataset_id, edge)

    return {dataset_id for dataset_id, edge in primary.items() if edge.value_relation == enums.ValueRelationChoices.CATEGORIZED.value}


def adjacency_of(edges, *, at: dict[str, int] | None = None, admit_scoped: bool = False, require_affine: bool = False) -> dict[int, list[tuple["models.Transformation", bool, int]]]:
    """Build the BFS adjacency of an edge collection.

    Forwards, unless the edge says there is nothing to walk: an UNMAPPABLE edge relates
    two systems while declaring that no point of one corresponds to a point of the other,
    so a path across it would be composing a map out of a stated non-correspondence.
    Backwards only if the edge has an inverse to offer -- a rank-changing edge does not,
    and neither does a warp field at any rank.

    ``at`` is where the caller is standing -- ``{"c": 2}`` -- and it gates the per-index edges
    through :func:`selector_admits`. Omitted, selector-scoped edges are simply not in the
    adjacency, so every walk that does not care about them behaves exactly as it did before they
    could be written. This is the *only* place the selector is consulted during a search: the
    universe fetch does not depend on ``at`` at all, which is what lets one fetched universe
    answer for several coordinates without rebuilding, and keeps the per-request memo intact.

    ``require_affine`` narrows the *forward* edges to those with a closed affine form
    (:func:`is_condensable`), so a walk over the result cannot return a route `condense_path`
    would refuse. The reverse edges need no narrowing: :func:`is_reverse_traversable` already
    demands an invertible kind and equal rank, which is closed-form by construction. Passed by
    the placement gate, which must not accept a layer no renderer can draw, and by the first
    pass of `SceneGraph.placement_path`, which prefers such a route where one exists.

    ``admit_scoped`` builds the adjacency of *existence* instead: scoped edges are in it
    whatever ``at`` says. Only the callers asking whether a thing is placed at all may pass it
    -- :func:`is_placeable_in` and the two reasons an unplaced layer is given -- because the
    resulting path is not a map. Composing one without fixing the coordinate would state a
    per-channel correction as though it held on every channel.
    """
    adjacency: dict[int, list[tuple[models.Transformation, bool, int]]] = {}
    seen: set[int] = set()
    for edge in edges:
        if edge.pk in seen or not edge.input_id or not edge.output_id:
            continue
        seen.add(edge.pk)
        if not selector_admits(edge, at, admit_scoped=admit_scoped):
            continue
        if is_traversable(edge) and (not require_affine or is_condensable(edge)):
            adjacency.setdefault(edge.input_id, []).append((edge, False, edge.output_id))
        if is_reverse_traversable(edge):
            adjacency.setdefault(edge.output_id, []).append((edge, True, edge.input_id))
    return adjacency


def create_identity_registration(
    *,
    input_system: "models.CoordinateSystem",
    world: "models.CoordinateSystem",
    shared: list[str],
    name: str,
    validity: str,
    ctx: CreationContext,
) -> "models.Transformation":
    """One identity placement edge on the named shared axes.

    A BY_DIMENSION wrapper around an IDENTITY child, because that is the only shape that
    can say "these axes correspond one-to-one, and I claim nothing about the rest" -- a
    square edge between systems of different rank cannot.

    Its one caller in the product is the minting of a scene's annotation collection, which
    gives the collection a space copying the world's axes and then states, with this edge,
    that the two correspond -- exact by construction, so VALIDATED. Nothing else here
    fabricates a placement: a registration over data that already exists is authored
    explicitly, through ``createTransformation``.

    ``validity`` is required, and used to default to UNKNOWN for the deleted scene bootstrap,
    which was assuming units. `PlacementValidity.UNKNOWN`'s description now promises the
    server writes it nowhere -- a default here is how that promise gets broken by someone
    who simply did not pass the argument.
    """
    edge = models.Transformation.objects.create(
        kind=enums.TransformKindChoices.BY_DIMENSION.value,
        name=name,
        input=input_system,
        output=world,
        input_axes=shared,
        output_axes=shared,
        params={},
        validity=validity,
        creator=ctx.user,
        organization=ctx.organization,
    )
    models.Transformation.objects.create(
        kind=enums.TransformKindChoices.IDENTITY.value,
        parent=edge,
        order=0,
        params={},
        creator=ctx.user,
        organization=ctx.organization,
    )
    return edge


def lineage_graph(
    root: "models.CoordinateSystem",
    *,
    organization: "Organization",
    max_depth: int | None = None,
) -> tuple[list, list["models.Transformation"]]:
    """Every container related to this one by derivation, and the edges between them.

    The lineage counterpart of :func:`traverse`, and the difference is which edges are
    *walked*: that one crosses every edge touching a space, so a registration pulls in
    everything else registered into the same world -- which is the neighbourhood, not the
    provenance. This crosses **derivation edges only** (:func:`is_derivation_edge`), so what
    comes back is exactly "what was this computed from, and what was computed from it",
    transitively, in both directions.

    Both directions on purpose. Asking a source what came out of it and asking a product
    what went into it are the same graph read from two ends, and a client standing on a
    mask wants the image above it *and* the measurement table below it.

    **Kind-blind, like `derivation_edges` and unlike `lineage_ancestors`.** The latter walks
    the *spatial* lineage -- who places whom -- and stops at an UNMAPPABLE primary, because
    data whose geometry did not survive inherits no placement. This is the *historical*
    lineage, where an UNMAPPABLE edge is the whole point: "this came from that, and the
    geometry did not survive" is the fact the kind exists to record, and dropping it here
    would break the chain exactly where a measurement table hangs off its instance mask.
    Each edge carries its own `kind`, so a client wanting only the placing chain filters on it.

    One query per generation, and the nodes come back as *containers* rather than spaces --
    a lineage is a story about data, and a dataset's grid, its levels and its lenses are one
    node in it rather than three.
    """
    start = container_map([root.pk]).get(root.pk)
    if start is None or start[0] == "system":
        # A world belongs to no container, so nothing was derived from it and nothing
        # derives from it. An empty graph, not an error: asking is reasonable.
        return [], []

    reached: set[tuple] = {start}
    frontier: set[tuple] = {start}
    edges: dict[int, models.Transformation] = {}
    depth = 0

    while frontier and (max_depth is None or depth < max_depth):
        touching = list(
            models.Transformation.objects.filter(parent__isnull=True, organization=organization)
            .filter(container_q(frontier, field="input") | container_q(frontier, field="output"))
            .distinct()
            .select_related("input", "output")
        )
        keys = _keys_for(touching)

        discovered: set[tuple] = set()
        for edge in touching:
            child = keys.get(edge.input_id) if edge.input_id else None
            if child is None or not is_derivation_edge(edge, of_container=child, keys=keys):
                continue
            parent = keys.get(edge.output_id) if edge.output_id else None
            if parent is None:
                continue
            edges[edge.pk] = edge
            # A cycle is a graph the walk must survive rather than an error: `reached` is
            # what makes it terminate.
            for endpoint in (child, parent):
                if endpoint not in reached:
                    reached.add(endpoint)
                    discovered.add(endpoint)

        frontier = discovered
        depth += 1

    # Both endpoints inside the component, so no edge dangles off a node that is not in
    # `nodes` -- at a `maxDepth` cutoff the boundary edges are precisely the ones that would.
    keys = _keys_for(edges.values())
    kept = [edge for edge in edges.values() if keys.get(edge.input_id) in reached and keys.get(edge.output_id) in reached]

    by_kind: dict[str, list[int]] = {}
    for kind, pk in reached:
        by_kind.setdefault(kind, []).append(pk)

    nodes: list = []
    # Presentation order, deduplicated: the three models sharing the `dataset` key are one
    # node kind, so this walks the keys in `CONTAINERS` order rather than the models.
    for key in dict.fromkeys(container.key for container in CONTAINERS):
        pks = by_kind.get(key)
        if not pks:
            continue
        model = MODEL_BY_KEY[key]
        query = model.objects.filter(pk__in=pks)
        # The containers escape to a client whole, so a co-tenant's must not: the same
        # reason `SpaceGraph` scopes and `SceneGraph`, which returns edges, does not.
        if hasattr(model, "organization"):
            query = query.filter(organization=organization)
        nodes.extend(query.select_related("coordinate_system").order_by("pk"))

    return nodes, sorted(kept, key=lambda edge: edge.pk)


def traverse(
    root: "models.CoordinateSystem",
    *,
    organization: "Organization",
    max_depth: int | None = None,
) -> tuple[list["models.CoordinateSystem"], list["models.Transformation"]]:
    """The connected component around a coordinate system: every system it reaches, and every edge between them.

    Reachability here is **undirected**, and deliberately so. Direction is a fact about how an
    edge composes, not about what it touches: standing on a physical space and asking what
    transforms relate to it, the answer plainly includes the pixel-to-physical edge that points
    *into* it. Walking only forward would return the empty set for exactly the systems a user
    is most likely to start from. The edges come back with their true stored direction and
    their axis names, so a client can still tell what is invertible (`is_reverse_traversable`)
    and what is not -- which is the placement question, and a different one.

    This does not compose anything, and it is not scene-scoped. It hands back the subgraph;
    what to do with it is the client's, in keeping with the rest of this module.

    The walk is batched -- one query per level of the search rather than one per node -- so a
    graph of any width costs O(depth) queries, and the two closing queries fetch the systems
    with their axes and the edges with everything a `Transformation` selection can ask for.
    Without those the discovery query would be a per-edge N+1 the moment a client selected
    `inputAxes` or a SEQUENCE's `children`: this returns a list, and a plain list is invisible
    to the optimizer.
    """
    reached: set[int] = {root.pk}
    frontier: set[int] = {root.pk}
    depth = 0

    while frontier and (max_depth is None or depth < max_depth):
        # Only the endpoints matter for the walk, so do not drag whole rows through it.
        endpoints = models.Transformation.objects.filter(parent__isnull=True, organization=organization).filter(Q(input_id__in=frontier) | Q(output_id__in=frontier)).values_list("input_id", "output_id")

        discovered: set[int] = set()
        for input_id, output_id in endpoints:
            for endpoint_id in (input_id, output_id):
                # A cycle (any loop in the graph) is one the walk must survive, not an
                # error: `reached` is what makes the search terminate.
                if endpoint_id is not None and endpoint_id not in reached:
                    reached.add(endpoint_id)
                    discovered.add(endpoint_id)

        frontier = discovered
        depth += 1

    # The residents come along too. This returns a plain list, which is invisible to the
    # optimizer, so a client selecting `residents` would otherwise pay six reverse queries
    # per node -- the same reason `axes` is prefetched here rather than left to the field.
    systems = list(
        models.CoordinateSystem.objects.filter(pk__in=reached)
        .prefetch_related("axes", *RESIDENT_RELATIONS)
        .order_by("pk")
    )

    # Both endpoints inside the component, so no edge dangles off a node that is not in
    # `systems` -- at a `maxDepth` cutoff the boundary edges are precisely the ones that
    # would.
    edges = list(
        models.Transformation.objects.filter(
            parent__isnull=True,
            organization=organization,
            input_id__in=reached,
            output_id__in=reached,
        )
        .select_related("input", "output", "parent")
        .prefetch_related("children", "input__axes", "output__axes", "parent__input__axes", "parent__output__axes")
        .order_by("pk")
    )

    return systems, edges


#: A space **nothing lives in**: a pure reference frame, a world. The residence-model
#: successor to "every owner FK is null" -- the same shape, read through the reverse
#: relations -- and it survives the loss of the fact/claim split because it is a better rule
#: than the one it replaces: it names what a world *is* rather than how its edges were made.
#:
#: Used to keep a walk from ever *standing on* such a space. Excluding only the edges that
#: point into one is not enough: a single stray edge back out would put it in the frontier,
#: and the next level would pull in every dataset registered there.
#:
#: **Derived from `RESIDENT_RELATIONS`, not written out.** It used to be a hand-written list of
#: six ``__isnull`` keys beside a `CONTAINERS` tuple whose whole purpose was to be the single
#: copy of that list -- and the two drifted exactly as that arrangement invites: `SparseDataset`
#: was added to the model layer and never to this dict, so a space holding only a sparse dataset
#: answered "nothing lives here" and was refused as a world. Deriving it means a container is
#: still one line in `CONTAINERS` and nothing else, which is what that tuple promises.
_UNINHABITED: dict[str, bool] = {f"{related_name}__isnull": True for related_name in RESIDENT_RELATIONS}


def fact_paths(
    root: "models.CoordinateSystem",
    *,
    organization: "Organization",
    max_depth: int | None = None,
) -> dict[int, list[tuple["models.Transformation", bool]]]:
    """Every system fact-reachable from ``root``, each with its ``(edge, inverted)`` path.

    The scene-independent sibling of :func:`traverse`: the same batched frontier walk, but
    over the **fact component** only -- derivations, pyramid levels, lenses, physical spaces --
    so a probe on a source image can find the instance mask derived from it. Three refusals
    define the component. Registrations are never crossed and a SHARED system is never even
    stood on (either side, see ``_UNINHABITED``): which claims compose is a scene's
    say-so, and this walk has no scene. UNMAPPABLE never walks, in either direction. And a
    FIELD edge is payload, not connectivity -- it is what a caller collects *at* the reached
    systems, and crossing it would put a table's index space in the frontier.

    Two phases. The frontier walk over-reaches on purpose (it knows endpoints, not
    primaries or invertibility); the second fetch pulls every edge *touching* the reached
    set -- one endpoint, deliberately, because an UNMAPPABLE primary's far side is
    unreached. Connectivity then keeps only the edges with *both* endpoints inside the
    reached set -- the frontier walk already refused to enter an uninhabited space, so an
    edge leading into one is an edge out of this probe's world -- :func:`adjacency_of`
    refuses the untraversable directions, and a single BFS tree yields one deterministic
    shortest path per reached system. Nothing is composed: the
    steps come back in stored direction with the inversions flagged, exactly like
    ``pathToWorld``, and composing them is the client's job for the reason it always is.
    """
    reached: set[int] = {root.pk}
    frontier: set[int] = {root.pk}
    depth = 0

    while frontier and (max_depth is None or depth < max_depth):
        endpoints = (
            models.Transformation.objects.filter(parent__isnull=True, organization=organization)
            .filter(Q(input_id__in=frontier) | Q(output_id__in=frontier))
            .exclude(kind__in=[enums.TransformKindChoices.UNMAPPABLE.value, enums.TransformKindChoices.FIELD.value])
            .exclude(**{f"input__{lookup}": value for lookup, value in _UNINHABITED.items()})
            .exclude(**{f"output__{lookup}": value for lookup, value in _UNINHABITED.items()})
            .values_list("input_id", "output_id")
        )

        discovered: set[int] = set()
        for input_id, output_id in endpoints:
            for endpoint_id in (input_id, output_id):
                if endpoint_id is not None and endpoint_id not in reached:
                    reached.add(endpoint_id)
                    discovered.add(endpoint_id)

        frontier = discovered
        depth += 1

    edges = list(
        models.Transformation.objects.filter(parent__isnull=True, organization=organization)
        .filter(Q(input_id__in=reached) | Q(output_id__in=reached))
        .select_related("input", "output")
        .prefetch_related("children", "input__axes", "output__axes")
        .order_by("pk")
    )

    connectivity = [edge for edge in edges if edge.kind != enums.TransformKindChoices.FIELD.value and edge.input_id in reached and edge.output_id in reached]
    parents = _bfs_tree(adjacency_of(connectivity), root.pk, max_depth=max_depth)

    return {pk: _steps_from_parents(parents, root.pk, pk) for pk in parents}


def walk_towards_intrinsic(system: "models.CoordinateSystem") -> tuple[list["models.Transformation"], "models.CoordinateSystem"]:
    """The composable edges from a system down towards intrinsic pixels, and where they end.

    **One definition, not one traversal.** A bounding box, the frame it is denominated in and
    the chain version recorded beside it are three halves of one fact, and three *different*
    walks could disagree and let a box be labelled with a frame it is not in. So this returns
    both the edges and the endpoint, and :func:`path_to_intrinsic`, :func:`intrinsic_frame` and
    :func:`transform_version` are three readings of one answer. They each call this afresh
    rather than sharing a result, so writing an annotation walks the chain more than once --
    correct, but not free, and worth memoizing per request if it ever shows up in a profile.

    Intrinsic pixel space is scene-independent, unit-independent and always defined, which is
    exactly why the ROI bounding box is expressed against it rather than against a scene's
    world or a physical space: world is scene-owned, and a physical space's edge can be
    refined -- pixel space never moves.

    **The walk stops rather than fails**, and there are three ways to stop short of pixels:

    - *Nowhere left to go.* A world or a physical space's edges point away from intrinsic, and
      a freestanding collection has no derivation at all. Its own space is the honest frame
      for its boxes.
    - *A cycle.* Refusing to go round again is the whole requirement; where it stops is a
      frame like any other.
    - *An edge with no fixed-rank matrix* (:func:`coords.has_matrix`) -- a BY_DIMENSION
      registration or a rank-changing derivation. Crossing it would mean inventing an extent
      along an axis the edge deliberately says nothing about.

    Stopping short used to be a ``ValueError`` that :func:`intrinsic_chain` swallowed into "no
    chain", which threw away the hops that *were* composable while :func:`intrinsic_frame`
    kept walking -- so the box sat in the frame it was drawn in and the frame column named a
    system one or more hops downstream. The BY_DIMENSION case did not even get that far: it
    raised out of the composer, and every draw into the collection behind such an edge 500'd.
    """
    dataset = system_dataset(system)

    edges: list[models.Transformation] = []
    current = system
    seen: set[int] = set()

    # `datasets` is the reverse FK, and it is the whole test: a mesh collection's or a table's
    # native space is a pixel grid too, but only an *array dataset* living in a space marks the
    # grid this walk terminates at -- a collection's space still has a derivation edge to cross.
    while current is not None and not current.datasets.exists():
        if current.pk in seen:
            break
        seen.add(current.pk)

        edge = _edge_towards_intrinsic(current, dataset)
        if edge is None or edge.output is None or not coords_logic.has_matrix(edge.kind):
            break

        edges.append(edge)
        current = edge.output

    return edges, current


def path_to_intrinsic(system: "models.CoordinateSystem") -> list[tuple[str, dict]]:
    """The chain :func:`walk_towards_intrinsic` composes, as (kind, params) for `to_matrix`.

    Empty when the system is already an intrinsic one, and empty when the first edge out of it
    is one the walk will not cross.
    """
    return [_edge_params(edge) for edge in walk_towards_intrinsic(system)[0]]


def intrinsic_frame(system: "models.CoordinateSystem") -> "models.CoordinateSystem":
    """The system a box drawn in this one is *stored* against: where the walk ends.

    The system itself when the walk crosses nothing, which is the common case for a
    collection: a drawing space registered into a world, or derived across a rank change,
    keeps its boxes in its own coordinates.
    """
    return walk_towards_intrinsic(system)[1]


def record_bbox_frame(collection: "models.AnnotationCollection", system: "models.CoordinateSystem") -> None:
    """Name the frame this collection's stored boxes will be in, once, at creation.

    Named now, while the answer is unambiguous, because every box the collection stores is a
    set of numbers against it. Recovering it later means re-walking the chain, and a second
    copy of that walk is a second chance to name the wrong frame.

    Stored only when the frame is a system *other* than the collection's own -- see the
    field, where a self-reference under PROTECT would make the collection undeletable.

    One writer for both creation paths. The explicit path did not write it at all, so a
    collection with a composable derivation stored its boxes in the dataset's pixel grid
    while ``effective_bbox_system`` answered "my own space" -- and the spatial queries that
    only compare within one frame compared two frames.
    """
    frame = intrinsic_frame(system)
    if frame is None or frame.pk == system.pk:
        return
    collection.bbox_system = frame
    collection.save_without_historical_record(update_fields=["bbox_system"])


def _edge_towards_intrinsic(system: "models.CoordinateSystem", dataset: "models.ArrayDataset | None") -> "models.Transformation | None":
    """The edge leading out of a system and *towards the data*, never out into a scene.

    Ordered by pk so the choice between two candidates is deterministic rather than
    whatever the database happens to return first. An edge nothing can be walked across
    is not a candidate: an UNMAPPABLE edge out of this system leads nowhere a coordinate
    can follow, and taking it would compose an ROI's box through a map that does not
    exist.

    **Two populations of system, and the second used to have no rule at all.** A system that
    belongs to a dataset must stay inside it: a registration points *out* of the dataset --
    from a lens, an array or the intrinsic system into some scene's world -- and it is not
    ordered behind the edge that goes up towards intrinsic, so an unfiltered "first edge out
    of this system" can pick it. A system that belongs to *no* dataset -- a collection's
    drawing space -- read ``dataset is None`` as "then any edge will do", which is the same
    hole with the guard switched off: the scene-minted annotation collection's only edge is
    its registration into the world, so the walk took it every time. It got away with it only
    because the world dead-ended one hop later and the whole chain was discarded.

    :func:`collection_derivation_edges` is the rule for that second population, and it is the
    same predicate ``derivedFrom`` reports: an edge into a shared space lands in no container,
    so it is a placement, not a lineage, and this walk has no business crossing it.
    """
    if dataset is None:
        return next((edge for edge in collection_derivation_edges(system) if edge.output is not None and is_traversable(edge)), None)

    candidates = models.Transformation.objects.filter(input=system, parent__isnull=True).select_related("output").order_by("pk")

    for edge in candidates:
        if edge.output is None or not is_traversable(edge):
            continue
        if system_dataset(edge.output) == dataset:
            return edge
    return None


def _edge_params(edge: "models.Transformation") -> tuple[str, dict]:
    """An edge as (kind, params), in the shape :func:`coords.to_matrix` expects.

    A SEQUENCE's children are flattened into one params dict. A MAP_AXIS's permutation is
    written out as an `affine`: it lives in the `input_axes` / `output_axes` *columns*,
    which `to_matrix` never sees, so composing it any other way would mean threading two
    more arguments through every caller for the one kind that needs them.
    """
    if edge.kind == enums.TransformKindChoices.MAP_AXIS.value:
        axis_order = [axis.name for axis in edge.input.axes.all()] if edge.input else []
        return edge.kind, {"affine": coords_logic.permutation_matrix(edge.input_axes or [], edge.output_axes or [], axis_order)}

    if edge.kind != enums.TransformKindChoices.SEQUENCE.value:
        return edge.kind, edge.params

    params: dict = {}
    for child in edge.children.order_by("order"):
        params.update(child.params)
    return edge.kind, params


def _edge_step(edge: "models.Transformation", *, inverted: bool = False) -> "coords_logic.AxedStep":
    """An edge as an `AxedStep`: its map, plus both endpoints' full axis orders.

    Deliberately not built on :func:`edge_axis_names`, which returns the *stored subset* for a
    BY_DIMENSION edge. A rank-changing push needs both -- the subset, to know which axes the
    parameters act on, and the systems' full orders, to know which of the rest pass through
    and which the edge never mentions -- and one list cannot be both.

    ``inverted`` is the flag a placement path carries beside each edge, and it is undone here
    rather than by each composer: :func:`~core.logic.coords.invert_step` is the one place a
    backwards step becomes a forward one, so no two callers can disagree about what walking
    an edge the other way means. It raises for a step with no inverse -- which the walk never
    flags, `adjacency_of` offering an edge backwards only where `is_reverse_traversable`
    holds, so this is a guard rather than a branch.
    """
    children: tuple[tuple[str, dict], ...] = ()
    if edge.kind in _COMPOSITE_KINDS:
        children = tuple((child.kind, child.params) for child in sorted(edge.children.all(), key=lambda child: (child.order, child.pk)))

    # Each endpoint's axes read once. `axes.all()` builds a fresh queryset per call unless a
    # prefetch upstream has filled the cache, and this runs once per edge per placement per
    # scene -- four queries where two will do is not a rounding error on the extent walk.
    input_axes_rows = list(edge.input.axes.all()) if edge.input else []
    output_axes_rows = list(edge.output.axes.all()) if edge.output else []

    step = coords_logic.AxedStep(
        kind=edge.kind,
        params=edge.params or {},
        input_axes=tuple(axis.name for axis in input_axes_rows),
        output_axes=tuple(axis.name for axis in output_axes_rows),
        # Both endpoints' units, positionally parallel to their names. Read here rather than
        # left to the composer because this is the one place that has the ORM rows: a step is
        # the last thing that knows which two *systems* it came from, and a unit pair is a
        # property of that pair. `coords_logic.step_forms` uses them for pass-through axes
        # only -- see `_pass_through_factor` for why that is the only defensible scope.
        input_units=tuple(axis.unit for axis in input_axes_rows),
        output_units=tuple(axis.unit for axis in output_axes_rows),
        acts_on_input=tuple(edge.input_axes) if edge.input_axes else None,
        acts_on_output=tuple(edge.output_axes) if edge.output_axes else None,
        children=children,
    )
    return coords_logic.invert_step(step) if inverted else step


@dataclasses.dataclass(frozen=True)
class CondensedPlacement:
    """A whole placement path composed into one affine map, and the axes it is written over.

    ``matrix`` is M x (N+1), rows outermost, the layout an ``AFFINE`` edge's ``affine``
    already uses: columns in ``input_axes`` order, the last column the translation.
    ``output_axes`` names the rows, and names **only the destination axes the path actually
    constrains** -- an axis it says nothing about has no row, never a zero one. ``total`` is
    whether that covers the destination's axes.
    """

    matrix: list[list[float]]
    input_axes: list[str]
    output_axes: list[str]
    total: bool


def condense_path(
    steps: list[tuple["models.Transformation", bool]],
    *,
    source_axes: list[str],
    destination_axes: list[str],
) -> CondensedPlacement:
    """Compose a placement path into one affine map, or raise saying which edge stopped it.

    The server does not *store* a composed placement and this does not change that: the same
    data under two registrations still has two answers, and this is only ever called with a
    path that already has one destination (a layer's, which belongs to one scene). What it
    removes is every client reimplementing the arithmetic this module already owns -- and
    getting it wrong in the same two places, by zero-filling the axes a BY_DIMENSION says
    nothing about, and by being unable to invert a step at all.

    Composed with :func:`~core.logic.coords.compose_forms`, not `compose`: `to_matrix` raises
    for a BY_DIMENSION, which is the shape of every ordinary registration, so a fixed-rank
    composition would fail on the common case. The axis-keyed form has no such gap and is
    what makes a partial answer expressible at all.

    Each step is built separately so a failure can name the edge that caused it. Left to the
    bare `NonAffineTransformError`, the message would be about a matrix; a client wants to
    know which registration to go and fix.

    The two endpoints arrive as **axis name lists, not systems**, and deliberately: reading
    `system.axes.all()` here would be one query per call, which is one query per layer, which
    is exactly the shape `test_scene_placements_are_flat_in_layer_count` forbids. The caller
    is the one that knows how to get them without paying that -- `SceneGraph` prefetches the
    layers' source systems and memoizes the world's, because the world is one object for the
    whole scene.
    """
    axed: list[coords_logic.AxedStep] = []
    for edge, inverted in steps:
        try:
            step = _edge_step(edge, inverted=inverted)
            # Asked here, and thrown away, purely so the failure carries an edge id: a step
            # with no closed form (a FIELD, whose map is an array) raises from inside
            # `compose_forms`, which sees a list of steps and no rows, and the resulting
            # message is about a matrix when what a client needs is which registration to go
            # and fix. Recomposed below rather than threaded through -- these are matrices of
            # rank four at most, and one honest error is worth more than one saved multiply.
            coords_logic.step_forms(step)
        except coords_logic.NonAffineTransformError as error:
            raise coords_logic.NonAffineTransformError(
                f"This placement does not condense into one affine map: transformation {edge.pk} ({edge.kind}{', walked backwards' if inverted else ''}) {error}"
            ) from error
        axed.append(step)

    forms = coords_logic.compose_forms(axed, source_axes)
    matrix, rows = coords_logic.forms_to_matrix(forms, destination_axes)
    return CondensedPlacement(matrix=matrix, input_axes=source_axes, output_axes=rows, total=len(rows) == len(destination_axes))


def dataset_behind(system: "models.CoordinateSystem") -> "models.ArrayDataset | None":
    """The dataset whose pixels this space shows, following one edge back if nothing lives here.

    A physical space has **no residents** (RFC-9): it is a frame, and the edge from the
    dataset's own grid is the only thing relating the two. So "which dataset is this a view
    of" cannot be answered by looking at the space alone -- it is the inverse of
    :func:`physical_neighbours`, one hop upstream.

    Residents first, so a dataset's own grid answers immediately and without a query on the
    edge table. Only a frame nothing lives in pays for the hop.
    """
    resident = system_dataset(system)
    if resident is not None:
        return resident

    incoming = (
        models.Transformation.objects.filter(output=system, parent__isnull=True)
        .exclude(kind=enums.TransformKindChoices.UNMAPPABLE.value)
        .select_related("input")
        .order_by("pk")
    )
    for edge in incoming:
        if edge.input is not None and (dataset := system_dataset(edge.input)) is not None:
            return dataset
    return None


def physical_neighbours(system: "models.CoordinateSystem") -> list["models.CoordinateSystem"]:
    """The spaces one edge out of this one whose axes carry units: its physical frames.

    What replaced `dataset.calibrations` (RFC-9). A physical space is not a thing a dataset
    owns -- it is an ordinary space with an edge into it -- so "does this dataset have a
    physical interpretation, and in what space" is a question about the graph, answered by
    looking one hop out and asking whether the axes over there carry units.

    Ordered by edge pk, so a caller that must pick one picks the first authored, and a caller
    that finds several knows the choice is real rather than arbitrary.
    """
    edges = (
        models.Transformation.objects.filter(input=system, parent__isnull=True)
        .exclude(kind=enums.TransformKindChoices.UNMAPPABLE.value)
        .select_related("output")
        .prefetch_related("output__axes")
        .order_by("pk")
    )
    found: list[models.CoordinateSystem] = []
    for edge in edges:
        target = edge.output
        if target is None or target.pk == system.pk:
            continue
        axes = list(target.axes.all())
        if axes and all(axis.unit for axis in axes):
            found.append(target)
    return found


def transform_version(system: "models.CoordinateSystem") -> int:
    """How many times the chain between a system and its intrinsic space has been written.

    Recorded on an ROI as provenance -- what the geometry was authored against. It
    is never used to resolve a coordinate; it only tells you whether the chain has
    moved under the ROI since. Only equality is meaningful: two readings agreeing means
    nothing on the chain was written between them. The number itself counts nothing a
    client should interpret.

    **Read from the history, not from a counter.** Every edge carries `provenance`, so a
    save already writes a history row; a `version` column beside it was a second record of
    the same fact that every writer had to remember to keep, and only `updateTransformation`
    did. Counting the rows cannot be forgotten, and it cannot disagree with the audit trail
    the same edge publishes as `provenanceEntries`.

    **A rename counts.** Any save writes a history row, so renaming an edge now reads as
    the chain having moved. That is the safe direction to be wrong in: a spurious "stale"
    costs a bounding-box recompute, while a missed one leaves a shape silently sitting
    where the registration no longer puts it.

    The edges of :func:`walk_towards_intrinsic` and no others, because the number has to
    describe the chain the box was actually pushed along: counting an edge the box never
    crossed reports drift in a map that had no part in the numbers, and missing one hides
    the drift that did move them. Zero for a system whose walk crosses nothing, which is not
    an error -- there is nothing denominated in its pixels to go stale.

    One query for the whole chain rather than one per edge: summing each edge's row count is
    the same number as counting the rows of all of them at once.
    """
    edge_ids = [edge.pk for edge in walk_towards_intrinsic(system)[0]]
    if not edge_ids:
        return 0
    # Through the reverse relation `provenance` installs rather than through the descriptor
    # itself: `Transformation.provenance` is a HistoricalRecords on the class and a manager on
    # an instance, so reading `.model` off the class is a runtime-only truth. The relation is
    # an ordinary field either way, and it is the same one `provenanceEntries` reads.
    historical = models.Transformation._meta.get_field("provenance_entries").related_model
    return historical.objects.filter(id__in=edge_ids).count()


def intrinsic_chain(system: "models.CoordinateSystem") -> list:
    """The resolved edge chain from a system down to intrinsic space, or [] when none exists.

    Resolving the chain is the per-*system* half of a bbox computation; a bulk write
    of many shapes into one system resolves it once and applies it per shape.

    A unit-carrying system (a physical space, a world) has no path down to a pixel space --
    those edges point away from intrinsic -- and answers []. A box is still meaningful in
    the system's own coordinates, which is what :func:`intrinsic_frame` then names.
    """
    return path_to_intrinsic(system)


def bbox_along_chain(chain: list, vectors: list[list[float]]) -> dict | None:
    """The bounding box of one shape's geometry, pushed along an already-resolved chain.

    Pushes **every corner** of the box, not just the two extremes. An
    affine-transformed AABB is not an AABB: under any rotation or shear, taking
    only min and max through the matrix yields a box that is strictly too small, so
    geometry that really is inside it tests as outside.
    """
    if not vectors:
        return None
    low, high = coords_logic.vectors_bbox(vectors)
    return coords_logic.transformed_bbox(low, high, chain)


def compute_intrinsic_bbox(system: "models.CoordinateSystem", vectors: list[list[float]]) -> dict | None:
    """The bounding box of a shape's geometry, pushed into the nearest intrinsic space."""
    return bbox_along_chain(intrinsic_chain(system), vectors)


def lens_source_system(lens: "models.Lens") -> "models.CoordinateSystem | None":
    """The space a lens' data is expressed in.

    An unsliced lens owns no system: its space is the dataset's intrinsic space.
    """
    return getattr(lens, "coordinate_system", None) or lens.dataset.intrinsic_coordinate_system


def layer_source_system(layer: "models.Layer") -> "models.CoordinateSystem | None":
    """The coordinate system a layer's data is expressed in, per kind.

    Every array-backed layer's data lives in its lens' space, an annotation layer's in its
    collection's drawing space, a mesh layer's in its collection's, and a
    point/track layer's in the space of the table dataset it draws from.

    Kinds group here exactly as they group on their source FK -- IMAGE, INTENSITY, RGB,
    PHASOR and LABEL over a lens; POINT and TRACK over a table -- and the grouping is the
    whole point: what distinguishes the members of a group is how each is *drawn*, which is
    not a spatial question and so does not reach this function. Read the list off
    :data:`core.enums.LENS_BACKED_KINDS` rather than spelling it again, so that adding a way of drawing
    array data cannot leave a layer with no space to be in.
    """
    if layer.kind in enums.LENS_BACKED_KINDS and layer.lens_id:
        return lens_source_system(layer.lens)
    if layer.kind == enums.LayerKindChoices.ANNOTATION.value and layer.annotation_collection_id:
        return getattr(layer.annotation_collection, "coordinate_system", None)
    if layer.kind == enums.LayerKindChoices.MESH.value and layer.mesh_collection_id:
        # A reverse one-to-one now, since the collection owns its system: Django raises
        # (an AttributeError subclass) rather than returning None when there is none.
        return getattr(layer.mesh_collection, "coordinate_system", None)
    if layer.kind == enums.LayerKindChoices.NETWORK.value and layer.network_collection_id:
        # Same shape as a mesh collection: the collection owns its space, and `derivedFrom`
        # relates it to whatever the network was traced in.
        return getattr(layer.network_collection, "coordinate_system", None)
    if layer.kind in (enums.LayerKindChoices.POINT.value, enums.LayerKindChoices.TRACK.value) and layer.table_dataset_id:
        # The table dataset owns its space, the same way a mesh collection does.
        return getattr(layer.table_dataset, "coordinate_system", None)
    return None


def system_dataset(system: "models.CoordinateSystem") -> "models.ArrayDataset | None":
    """The dataset a coordinate system belongs to, whichever owner it hangs off.

    A collection or table dataset's system belongs to no dataset *directly* -- a mesh
    collection, a feature table and a table dataset own their spaces -- so the dataset is
    the one its derivation edge lands in. That indirection is the price of owning the
    system, and it is what keeps a mesh or point layer bucketed with the dataset its data
    was extracted from, which is what lets the placement search find its way to world.

    Following the derivation edge regardless of kind is deliberate: for a measurement table
    the edge is UNMAPPABLE, so this still answers "that image", and the *walk* is what
    refuses to cross it. Bucketing is the scope of a search, not a claim about geometry;
    keeping the edge inside the scope is what lets the gate be the thing that says no.
    """
    dataset = next(iter(system.datasets.all()[:1]), None)
    if dataset is not None:
        return dataset
    lens = next(iter(system.lenses.all()[:1]), None)
    if lens is not None:
        return lens.dataset
    array = next(iter(system.data_arrays.all()[:1]), None)
    if array is not None:
        return array.dataset
    if collection_in(system) is not None:
        return collection_source_dataset(system)
    return None


def collection_derivation_edge(system: "models.CoordinateSystem") -> "models.Transformation | None":
    """The edge relating a collection's own system to the data it was derived from.

    Optional by design: a mesh in some absolute space, belonging to no dataset, has none,
    and that is a freestanding collection rather than an error.

    The earliest *derivation* edge by pk, not simply the earliest edge. It used to be the
    latter, which was both kind-blind and order-blind: a freestanding collection later
    registered into a world with ``createTransformation`` reported that **registration** as
    its ``derivedFrom``, under a description promising the edge back into the data it was
    computed from. Same predicate as :func:`derivation_edges`, so the two cannot disagree
    about what a derivation is.
    """
    return next(iter(collection_derivation_edges(system)), None)


def collection_derivation_edges(system: "models.CoordinateSystem") -> list["models.Transformation"]:
    """Every edge relating a collection's own system to data it was computed from, in order.

    A list, because a collection may name several sources exactly as a fused dataset does --
    the first by pk is the primary parent, which is the one that places it, and the rest are
    recorded facts that ``derivedFrom`` reports and no placement walk crosses.

    Screened by :func:`is_derivation_edge`, so a registration into a world -- which also
    leaves this system -- is not reported as something the collection came from.
    """
    candidates = list(models.Transformation.objects.filter(input=system, parent__isnull=True).select_related("output").order_by("pk"))
    if not candidates:
        return []

    keys = container_map({edge.output_id for edge in candidates if edge.output_id} | {system.pk})
    own = keys.get(system.pk)
    return [edge for edge in candidates if is_derivation_edge(edge, of_container=own, keys=keys)]


def collection_source_dataset(system: "models.CoordinateSystem") -> "models.ArrayDataset | None":
    """The dataset a collection's system was derived from, or None if it is freestanding."""
    edge = collection_derivation_edge(system)
    if edge is None or edge.output is None:
        return None
    return system_dataset(edge.output)


def _bfs_tree(
    adjacency: dict[int, list[tuple["models.Transformation", bool, int]]],
    source_pk: int,
    *,
    max_depth: int | None = None,
    target_pk: int | None = None,
) -> dict[int, tuple[int, "models.Transformation", bool] | None]:
    """The best-path parents map from a source: node -> (previous node, edge, inverted).

    **Best, not shortest.** This was an unweighted BFS -- fewest hops, ties broken by edge pk --
    and `validity` was not consulted by the search at all, only reported afterwards by
    `weakest_validity` as the weakest link on whatever path the walk happened to find. The
    consequence was the sharpest bug in the coordinate model: a one-hop UNKNOWN registration beat
    a two-hop chain of VALIDATED ones, and the layer then *reported* UNKNOWN. The system picked
    the worse answer and told you it was bad. Rival edges were deliberately permitted at write
    (see `build_registration_edge`) on the stated grounds that a best-path rule would rank them,
    and that rule was never written -- `docs/rfc9-residence.md` listed it under "Not done".

    **A widest path, not a shortest one.** How known a path is, is the *weakest* edge on it, so
    the quantity to optimise is a bottleneck rather than a sum, and the search maximises the
    minimum `_VALIDITY_RANK` along the path before it minimises hops. That key is monotone along
    any path -- a bottleneck only falls, a hop count only rises -- which is exactly the condition
    that makes greedy expansion correct, so this is a Dijkstra in shape even though its cost does
    not add.

    **Invariance is deliberately not in the cost.** `_INVARIANCE_RANK` sits right beside
    `_VALIDITY_RANK` and it is tempting to fold in, but the two answer different questions: how
    *known* a map is, versus what it *preserves*. A path through an AFFINE is not less
    trustworthy than one through an ISOMETRY, merely less constrained, and ranking by it would
    quietly prefer a rigid guess to a measured shear. It stays a reported property.

    Ties beyond that resolve on ``(edge pk, node pk)``, so the answer is stable across processes
    rather than dependent on dict iteration order. Stops early when the optional target is
    reached, or when the optional depth cap is hit.
    """
    best = enums.PlacementValidityChoices.VALIDATED.value
    source_key = (-_VALIDITY_RANK.get(best, 0), 0)

    parents: dict[int, tuple[int, "models.Transformation", bool] | None] = {source_pk: None}
    #: node -> (-bottleneck validity rank, hops) of the best path found to it so far.
    keys: dict[int, tuple[int, int]] = {source_pk: source_key}

    # (-bottleneck, hops, edge pk, node pk) -- the last two are pure tiebreak, so that two runs
    # over the same graph return the same path.
    heap: list[tuple[int, int, int, int]] = [(*source_key, 0, source_pk)]
    settled: set[int] = set()

    while heap:
        neg_bottleneck, hops, _, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled.add(node)
        if target_pk is not None and node == target_pk:
            break
        if max_depth is not None and hops >= max_depth:
            continue

        for edge, inverted, neighbor in sorted(adjacency.get(node, []), key=lambda step: step[0].pk):
            if neighbor in settled:
                continue
            # The bottleneck of the extended path: this edge's validity, or the path's, whichever
            # is weaker. `.get(..., 0)` reads an unknown label as the weakest, which is the safe
            # direction -- an unrecognised validity must not win a path.
            rank = min(-neg_bottleneck, _VALIDITY_RANK.get(edge.validity, 0))
            candidate = (-rank, hops + 1)
            if neighbor in keys and keys[neighbor] <= candidate:
                continue
            keys[neighbor] = candidate
            parents[neighbor] = (node, edge, inverted)
            heapq.heappush(heap, (*candidate, edge.pk, neighbor))

    return parents


def _steps_from_parents(
    parents: dict[int, tuple[int, "models.Transformation", bool] | None],
    source_pk: int,
    node: int,
) -> list[tuple["models.Transformation", bool]]:
    """Read the (edge, inverted) steps source -> node back out of a BFS parents map."""
    steps: list[tuple["models.Transformation", bool]] = []
    while node != source_pk:
        previous, edge, inverted = parents[node]
        steps.append((edge, inverted))
        node = previous
    return list(reversed(steps))


def _bfs_path(
    adjacency: dict[int, list[tuple["models.Transformation", bool, int]]],
    source_pk: int,
    target_pk: int,
) -> list[tuple["models.Transformation", bool]] | None:
    """The best path of (edge, inverted) steps from source to target, or None.

    *Best*, not shortest: :func:`_bfs_tree` maximises the weakest validity on the path and only
    then minimises hops. The name predates that rule.
    """
    if source_pk == target_pk:
        return []

    parents = _bfs_tree(adjacency, source_pk, target_pk=target_pk)
    if target_pk not in parents:
        return None
    return _steps_from_parents(parents, source_pk, target_pk)


# The placement questions -- where a layer sits in its scene, where each pyramid level sits,
# which systems a space reaches -- are searches over an edge universe built and searched by
# :class:`core.logic.scene_graph.SceneGraph` (per scene) and
# :class:`core.logic.space_graph.SpaceGraph` (per space). There are deliberately no
# module-level shims delegating to them from here: a shim constructs the graph directly and
# so bypasses the per-request memo those classes exist for, which is the per-layer rebuild
# `test_scene_placements_are_flat_in_layer_count` forbids. Reach a graph through its
# `for_request`.
