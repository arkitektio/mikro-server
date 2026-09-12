from kante.types import Info
import strawberry

from core import enums, types, models, scalars
from datalayer.datalayer import get_current_datalayer
import json

import kante
from pydantic import BaseModel, Field
from lightpath.inputs.types import LightpathGraphInput
from optikit.inputs import OptikitStateInput
from optikit.models import OptikitStateModel
from lightpath.inputs.models import LightpathGraphInputModel
from core.creation import CreationContext
from core.inputs.coords import AxisInput, AxisInputModel, DerivedFromInput, DerivedFromSpec
from core.inputs.file_link import SourceFileInput, SourceFileInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import file_link as file_link_logic
from core.logic import folder as folder_logic
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from core.mutations._generic import assert_can_delete, make_delete, self_owner, dataset_owner
from core.scoping import get_for_org
import logging

logger = logging.getLogger(__name__)


class AxisAnchorInputModel(BaseModel):
    axis: str
    value: int


@kante.pydantic_input(AxisAnchorInputModel, description="Input type for an axis anchor, which pins one axis to one discrete position")
class AxisAnchorInput:
    axis: str = strawberry.field(description="The axis to anchor to, e.g. 'x', 'y', 'z', 'c', or 't'")
    value: int = strawberry.field(description="The position to anchor the axis to, e.g. 0 for the first position along that axis")


class OmeMetadataInputModel(BaseModel):
    metadata_string: str = Field(..., description="The OME metadata as a JSON string")


@kante.pydantic_input(OmeMetadataInputModel, description="Input type for OME metadata")
class OmeMetadataInput:
    metadata_string: str = strawberry.field(description="The OME metadata as a JSON string")


class ValueHistogramInputModel(BaseModel):
    histogram: list[float] = Field(..., description="The histogram of the pixel values (y values)")
    bins: list[float] = Field(..., description="The bin indices of the histogram (x values)")
    min: float | None = Field(None, description="The minimum pixel value of the histogram")
    max: float | None = Field(None, description="The maximum pixel value of the histogram")
    p1: float | None = Field(None, description="The 1st percentile pixel value of the histogram")
    p99: float | None = Field(None, description="The 99th percentile pixel value of the histogram")


@kante.pydantic_input(ValueHistogramInputModel, description="Input type for a value histogram, which specifies the histogram of pixel values along certain dimensions to provide additional context about the distribution of pixel values in an image")
class ValueHistogramInput:
    histogram: list[float] = strawberry.field(description="The histogram of the pixel values (y values)")
    bins: list[float] = strawberry.field(description="The bin indices of the histogram (x values)")
    min: float | None = strawberry.field(default=None, description="The minimum pixel value of the histogram")
    max: float | None = strawberry.field(default=None, description="The maximum pixel value of the histogram")
    p1: float | None = strawberry.field(default=None, description="The 1st percentile pixel value of the histogram")
    p99: float | None = strawberry.field(default=None, description="The 99th percentile pixel value of the histogram")


class PhasorHistogramInputModel(BaseModel):
    axis: str = Field(..., description="The axis the phasor was taken over")
    counts: list[float] = Field(..., description="The flattened bins x bins density")
    # Optional with a None default rather than a real default, because the strawberry bridge
    # passes None through for an unset field; the defaults are applied where they are written.
    harmonic: int | None = Field(None, description="The harmonic the phasor was taken at")
    bins: int | None = Field(None, description="The resolution of the square (g, s) density grid")
    g_min: float | None = Field(None)
    g_max: float | None = Field(None)
    s_min: float | None = Field(None)
    s_max: float | None = Field(None)
    total: int | None = Field(None)
    calibrated: bool | None = Field(None)
    profile: list[float] | None = Field(None)


@kante.pydantic_input(PhasorHistogramInputModel, description="Input type for a phasor distribution: the 2D (g, s) density of a phasor taken over one axis at one harmonic, plus the summed profile it came from. Persisted so a client can pick a value range for a phasor overlay without reading the cube")
class PhasorHistogramInput:
    axis: str = strawberry.field(description="The axis the phasor was taken over, e.g. 'tau'. Must be a MICROTIME or SPECTRUM axis of the dataset")
    counts: list[float] = strawberry.field(description="The flattened bins x bins (g, s) density, row-major with s outermost")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic the phasor was taken at (default 1)")
    bins: int | None = strawberry.field(default=None, description="The resolution of the square density grid (default 256)")
    g_min: float | None = strawberry.field(default=None, description="The lower g bound of the grid (default 0)")
    g_max: float | None = strawberry.field(default=None, description="The upper g bound of the grid (default 1)")
    s_min: float | None = strawberry.field(default=None, description="The lower s bound of the grid (default 0)")
    s_max: float | None = strawberry.field(default=None, description="The upper s bound of the grid (default 0.6)")
    total: int | None = strawberry.field(default=None, description="The number of pixels that contributed, so counts can be normalized")
    calibrated: bool | None = strawberry.field(default=None, description="Whether the g/s were reference-corrected when computed (default false)")
    profile: list[float] | None = strawberry.field(default=None, description="The summed profile along the phasor axis: a decay for a MICROTIME axis, a spectrum for a SPECTRUM one")


class PhasorCalibrationInputModel(BaseModel):
    axis: str = Field(..., description="The axis the correction applies to")
    harmonic: int | None = Field(None, description="The harmonic the correction applies at")
    phase_offset: float | None = Field(None, description="The phase correction in radians")
    modulation_factor: float | None = Field(None, description="The modulation correction")
    reference: str | None = Field(None, description="What the correction was measured against")


@kante.pydantic_input(PhasorCalibrationInputModel, description="Input type for an instrument-response correction: the phase offset and modulation factor taking a raw phasor to a calibrated one")
class PhasorCalibrationInput:
    axis: str = strawberry.field(description="The axis the correction applies to, e.g. 'tau'. Must be a MICROTIME or SPECTRUM axis of the dataset")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic the correction applies at (default 1)")
    phase_offset: float | None = strawberry.field(default=None, description="The phase correction in radians, added to each pixel's phase")
    modulation_factor: float | None = strawberry.field(default=None, description="The modulation correction, multiplied into each pixel's modulus")
    reference: str | None = strawberry.field(default=None, description="What the correction was measured against, e.g. 'Rhodamine 6G, 4.1 ns'")


class LabelInputModel(BaseModel):
    label: str


@kante.pydantic_input(LabelInputModel, description="Input type for a label, which specifies a label to associate with a coordinate anchor or an image")
class LabelInput:
    label: str = strawberry.field(description="The label to associate with the coordinate anchor or image, which can provide additional context about the content of the image or the significance of the coordinate anchor")


class CoordinateAnchorInputModel(BaseModel):
    axis_anchors: list[AxisAnchorInputModel]
    microscope: OptikitStateModel | None = None
    ome_metadata: OmeMetadataInputModel | None = None
    value_histogram: ValueHistogramInputModel | None = None
    label: LabelInputModel | None = None
    light_graph: LightpathGraphInputModel | None = None
    phasor_histogram: PhasorHistogramInputModel | None = None
    phasor_calibration: PhasorCalibrationInputModel | None = None


@kante.pydantic_input(CoordinateAnchorInputModel, description="Input type for a coordinate anchor, which specifies a list of dimension anchors to anchor to")
class CoordinateAnchorInput:
    axis_anchors: list[AxisAnchorInput] = strawberry.field(description="A list of dimension anchors to anchor to, e.g. [{'axis': 'z', 'value': 0}, {'axis': 't', 'value': 5}] to anchor to the first position along the z dimension and the sixth position along the t dimension")
    microscope: OptikitStateInput | None = strawberry.field(default=None, description="Optional recorded microscope (Optikit) state to associate with the coordinate anchor: the hardware truth -- stage pose, environment, per-device settings -- at this coordinate, as composable typed input (quantities like '100.5 um' or '20 mW' where the setting carries a unit). An acquisition fact, recorded at ingest like the other spokes")
    ome_metadata: OmeMetadataInput | None = strawberry.field(default=None, description="Optional OME metadata to associate with the coordinate anchor, which can provide additional context about the axes being anchored to")
    value_histogram: ValueHistogramInput | None = strawberry.field(default=None, description="Optional value histogram to associate with the coordinate anchor, which can provide additional context about the distribution of pixel values along the anchored dimensions")
    label: LabelInput | None = strawberry.field(default=None, description="Optional label to associate with the coordinate anchor, which can provide additional context about the significance of the coordinate anchor or the content of the image at that coordinate")
    light_graph: LightpathGraphInput | None = strawberry.field(default=None, description="Optional lightpath graph to associate with the coordinate anchor, which can provide additional context about the optical path that was used to acquire the image at that coordinate")
    phasor_histogram: PhasorHistogramInput | None = strawberry.field(default=None, description="Optional phasor distribution to associate with the coordinate anchor, for a converter that already computed one. It is more usually attached after ingest with createPhasorHistogram, since computing it means reading the cube")
    phasor_calibration: PhasorCalibrationInput | None = strawberry.field(default=None, description="Optional instrument-response correction to associate with the coordinate anchor, taking a raw phasor at this coordinate to a calibrated one")


class ScaleInputModel(BaseModel):
    level: int
    array: str = Field(..., description="The array-like object to create the image from")
    scale_method: enums.ScaleMethod | None = None


@kante.pydantic_input(ScaleInputModel, description="Input type for one pyramid level: the array backing it, and how it was downsampled. Its scale factor is derived from its actual shape, never supplied")
class ScaleInput:
    """Input for one pyramid level."""

    level: int = strawberry.field(description="The level of the scale, where 0 is the highest resolution scale and higher levels are lower resolution scales")
    array: scalars.ArrayLike = strawberry.field(description="The array-like object to create the image from")
    scale_method: enums.ScaleMethod | None = strawberry.field(
        default=None,
        description="How this level's voxels were computed from the level above it. Stated, never derived -- nothing about two arrays says whether one was averaged or picked out of the other. **Required, and restricted to NEAREST or MODE, when this dataset's primary derivation is declared CATEGORIZED, or when any of its axes is an INDEX axis** -- an axis that enumerates objects: over an array of object ids every other method returns numbers that were not in the input, and an invented id is an object that does not exist",
    )


class CreateDatasetInputModel(BaseModel):
    data: str
    scales: list[ScaleInputModel]
    name: str
    axes: list[AxisInputModel]
    folder: str | None = None
    anchors: list[CoordinateAnchorInputModel] | None = None
    derived_from: list[DerivedFromSpec] | None = None
    source_files: list[SourceFileInputModel] | None = None


@kante.pydantic_input(CreateDatasetInputModel, description="Input type for creating an array dataset. Its axes are structural (name and kind); physical units, if known, arrive afterwards through createCoordinateSystem with a registrations entry naming the dataset")
class CreateArrayDatasetInput:
    """Input for creating an array dataset."""

    data: scalars.ArrayLike = strawberry.field(description="The array-like object to create the image from")
    scales: list[ScaleInput] = strawberry.field(description="The lower-resolution pyramid levels. Each level's absolute scale is derived from its actual shape against level 0's -- a pyramid whose axes do not halve cleanly is described correctly, and no caller can supply a wrong factor")
    name: str = strawberry.field(description="The name of the image")
    axes: list[AxisInput] = strawberry.field(
        description="The dataset's structural axes, in array order (slowest-varying first) -- they must describe the store's dimensions, and are checked against its shape. No ordering by type is required beyond that: (z, c, y, x) and (c, z, y, x) are both accepted as given. They carry no units: the intrinsic space is the pixel grid"
    )
    folder: strawberry.ID | None = strawberry.field(
        default=None,
        description="The folder to file this dataset in. Organisational only -- it says nothing about where the data sits in space. Defaults to the user's default folder",
    )
    anchors: list[CoordinateAnchorInput] | None = strawberry.field(
        default=None, description="Optional list of coordinate anchors to associate with the dataset, each pinning metadata spokes (OME metadata, histograms, labels) to specific positions along certain axes"
    )
    derived_from: list[DerivedFromInput] | None = strawberry.field(
        default=None,
        description="Optional statement of where this dataset's pixels came from: one entry per source lens -- a deconvolution or resample has one, a fusion of two channels or tiles has several -- each carrying the map back into that lens' space. Stored as edges of the coordinate graph, not as labels: the derived dataset then inherits its sources' placements, so refining a source's registration moves it too, and a layer over it resolves `pathToWorld` through a source. The order is the priority: the first entry is the primary parent (it drives `derivedFrom` order and the lineage root); later entries are additional sources whose edges are just as walkable. An UNMAPPABLE entry records history only and may not precede a mappable one",
    )
    source_files: list[SourceFileInput] | None = strawberry.field(
        default=None,
        description=(
            "Optional statement of which files this dataset's arrays were converted from -- the CZI or LIF a converter read to write this Zarr, named per series. **Not a "
            "`derivedFrom` entry, deliberately**: a derivation is an edge of the coordinate graph and every one of them relates two spaces, while a file has no space at all. "
            "This records lineage between bytes and data, claims no geometry, and leaves the graph untouched"
        ),
    )


def _parse_json_object(value: str | None, field: str) -> dict:
    """Parse a JSON-object string a client sent, and say so when it is not what it claims to be.

    The spoke columns are JSONFields, so the string must be a JSON *object*. It is an easy
    field to get wrong -- OME metadata is classically XML, and an empty upload is a string of
    whitespace -- and a bare `json.loads` reports every one of those the same way:
    "Expecting value: line 1 column 1 (char 0)", with no hint that it was talking about this
    field rather than, say, the zarr metadata read a few lines earlier.
    """
    if value is None or value.strip() == "":
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        head = value.strip()[:80]
        hint = " It looks like XML; OME-XML must be converted to JSON before it is sent." if head.startswith("<") else ""
        raise ValueError(f"{field} is not valid JSON ({exc}).{hint} It starts: {head!r}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must be a JSON object, not a {type(parsed).__name__}.")

    return parsed


def assert_pyramid_is_label_compliant(name: str, scales: list[ScaleInputModel], axes: list[AxisInputModel], derived_from: list | None) -> None:
    """A pyramid over object ids may only have been built by picking, never by averaging.

    The guard exists because the damage is silent and permanent. Downsample a mask with an
    area average and level 1 holds 41.5 where objects 41 and 42 meet -- an id belonging to
    no object, along every boundary in the image. Nothing later notices: the array is
    well-formed, it renders, and the phantom ids only show up as objects that cannot be
    looked up in the table the mask keys into. By then level 0 is the only trustworthy
    level and no server-side fix exists, because the original assignment is gone.

    So it is checked at the one moment it can be: when the levels are written, and on two
    signals rather than one.

    The first is the primary derivation's ``value_relation`` -- the same statement
    ``_infer_kind`` reads to bootstrap a label layer -- taken off the *input*, since the
    edges themselves are not written until after the levels exist.

    The second is an **INDEX axis**: a dimension that enumerates objects rather than
    measuring anything. It says the array is not intensities as loudly as CATEGORIZED does,
    and it says it through a door the first signal cannot see -- a mask ingested with no
    derivation at all states nothing about its values, and until this it was checked by
    nothing. It refuses more than it strictly must: an INDEX axis can never shrink between
    levels (``core.logic.coords._DOWNSAMPLABLE_TYPES``), so what a ``(object, y, x)`` pyramid
    downsamples is y and x, and a stack of per-object *intensity* crops is honestly built
    with AREA. That case is refused too, deliberately: the cost of a wrong refusal is a
    caller restating a method, and the cost of a wrong acceptance is a pyramid of ids that
    belong to nothing.

    Neither signal catches a mask that is declared later, by a ``keyedBy`` edge authored when
    its object table is created: by then the pyramid is already written, and refusing that
    edge would fail a *table*'s creation over a *mask*'s history without repairing anything.
    That case is reported instead, on ``ArrayDataset.pyramidIsLabelCompliant``.
    """
    primary = next(iter(derived_from or []), None)
    categorized = primary is not None and primary.value_relation == enums.ValueRelation.CATEGORIZED
    indexed = [axis.name for axis in axes if axis.type == enums.AxisType.INDEX]
    if not categorized and not indexed:
        return

    if categorized:
        claim = "is declared CATEGORIZED -- its values are object ids"
        short = "is declared CATEGORIZED"
    else:
        named = ", ".join(f"'{axis}'" for axis in indexed)
        noun = "axes" if len(indexed) > 1 else "axis"
        claim = f"carries the INDEX {noun} {named} -- {'they enumerate' if len(indexed) > 1 else 'it enumerates'} objects rather than measuring them"
        short = f"carries the INDEX {noun} {named}"

    allowed = ", ".join(sorted(enums.LABEL_COMPLIANT_SCALE_METHODS))
    for scale in scales:
        if scale.scale_method is None:
            raise ValueError(
                f"'{name}' {claim} -- so pyramid level {scale.level} must say how it was downsampled, and say one of {allowed}. "
                "A label pyramid built by averaging holds ids belonging to no object along every boundary, and nothing downstream can tell those apart from real ones."
            )
        if scale.scale_method.value not in enums.LABEL_COMPLIANT_SCALE_METHODS:
            raise ValueError(
                f"'{name}' {short}, so pyramid level {scale.level} may not have been downsampled with {scale.scale_method.value}: it returns values that were not in the input, and an invented id is an object that does not exist. Use one of {allowed}."
            )


def assert_axes_describe_the_store(axes: list, store: "models.ZarrStore") -> None:
    """Check the declared axes against what the zarr itself says, before anything is written.

    Two checks, and the second is the one that has never existed. ``ZarrStore.fill_info``
    reads ``dimension_names`` off the array and stores it (`datalayer/models.py:296`); it is
    published in the SDL (`datalayer/types.py:503`); and nothing has ever compared it to the
    caller's ``axes``. So a ``(z, y, x)`` store declared ``(x, y, z)`` was accepted, and the
    failure is not an error: the render axes are derived from the *position* of the spatial
    axes, so it renders transposed.

    That the names are redundant with the bytes is exactly why they are worth checking rather
    than dropping. The *type* is not redundant -- nothing in a zarr says an axis is TIME
    rather than SPACE -- so ``axes`` stays required; mapping ``{x, y, z} -> SPACE`` here
    would be convention-guessing, which is the thing this codebase argues against everywhere
    else. Declare the type, and the name is checked for free.

    Entry-wise, skipping nulls: zarr v3 permits a null per dimension, which
    :class:`~datalayer.base_models.ZarrMetadata` types as ``list[str | None] | None``. A null
    is the store declining to name that dimension, not a disagreement. A store with no
    ``dimension_names`` at all -- zarr v2, or written before the field existed -- is skipped
    entirely rather than refused: the check is on what the bytes say, and those bytes say
    nothing.
    """
    declared = [axis.name for axis in axes]

    if len(store.shape) != len(declared):
        raise ValueError(
            f"The data has {len(store.shape)} dimensions but {len(declared)} "
            f"{'axis was' if len(declared) == 1 else 'axes were'} declared "
            f"({', '.join(declared) or 'none'}). Every dimension of the array needs an axis: "
            "an axis is what gives a dimension a type, and the type is what decides how it is "
            "rendered, coarsened and composed."
        )

    named = store.dimension_names
    if not named:
        return

    disagree = [
        (index, stored, declared[index])
        for index, stored in enumerate(named)
        if stored is not None and stored != declared[index]
    ]
    if disagree:
        detail = "; ".join(
            f"dimension {index} is {stored!r} in the store and was declared {given!r}"
            for index, stored, given in disagree
        )
        raise ValueError(
            f"The declared axes do not describe this array: {detail}. The store names its "
            f"dimensions {list(named)} and the declaration reads {declared}. This is refused "
            "rather than reconciled because the failure would not be an error -- the render "
            "axes are derived from the *position* of the spatial axes, so a transposed "
            "declaration renders the wrong picture instead of raising. Reorder the axes to "
            "match the array, or transpose the array before uploading it."
        )


def create_array_dataset(
    info: Info,
    input: CreateArrayDatasetInput,
) -> types.ArrayDataset:
    """Create an array dataset, its coordinate systems and the edges placing every level in its intrinsic space."""
    model = input.to_pydantic()

    # Before anything is written: a pyramid the values forbid must not leave a dataset behind.
    assert_pyramid_is_label_compliant(model.name, model.scales, model.axes, model.derived_from)

    datalayer = get_current_datalayer()

    data_store = get_for_org(models.ZarrStore, info, id=model.data)
    data_store.fill_info(datalayer)

    base_shape = data_store.shape
    # An `assert`, until 2026-08-20: it vanished under `-O` and surfaced as an
    # AssertionError rather than as prose a caller could act on.
    assert_axes_describe_the_store(model.axes, data_store)

    axis_specs = [coords_logic.AxisSpec(name=axis.name, type=axis.type.value) for axis in model.axes]

    # The declared order is taken as given. It is the store's dimension order -- that is
    # what `assert_axes_describe_the_store` above has just checked it against -- and no
    # further ordering is required of it: (z, c, y, x) and (c, z, y, x) are ordinary ways
    # to write an acquisition, and `resolve_render_axes` reads neither the position of the
    # channel axis nor that of the time axis.

    ctx = CreationContext.from_info(info)
    # The space first, then the data that lives in it. Under residence nothing points from a
    # space back at its data, so there is no cycle to break and no second write: one INSERT
    # each, in the order the dependency actually runs.
    intrinsic = models.CoordinateSystem.objects.create(
        name=f"{model.name}/intrinsic",
        creator=ctx.user,
        organization=ctx.organization,
    )
    dataset = models.ArrayDataset.objects.create(
        name=model.name,
        coordinate_system=intrinsic,
        folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
        creator=ctx.user,
        organization=ctx.organization,
        **ctx.provenance_kwargs(),
    )
    graph_logic.create_pixel_axes(intrinsic, model.axes)

    levels = [(0, data_store)] + [(scale.level, get_for_org(models.ZarrStore, info, id=scale.array)) for scale in model.scales]
    scale_methods = {scale.level: scale.scale_method.value for scale in model.scales if scale.scale_method is not None}

    for level, store in levels:
        if level != 0:
            store.fill_info(datalayer)
            # The same check level 0 gets, and for the same reason: a level is the *same*
            # array at a coarser grid, so it has the same axes in the same order. A level
            # whose zarr names its dimensions differently is the transposition bug one zoom
            # down -- the dataset renders correctly until the viewer crosses into that level.
            # This was a second bare `assert`, on rank only, until 2026-08-20.
            try:
                assert_axes_describe_the_store(model.axes, store)
            except ValueError as error:
                raise ValueError(f"Pyramid level {level}: {error}") from None

        # Level 0 lives in the dataset's own grid -- it *is* that grid -- so it points at
        # the same space rather than getting a duplicate node joined by an all-ones SCALE.
        # Only a level whose space really differs gets one of its own.
        array_system = intrinsic
        if level != 0:
            array_system = models.CoordinateSystem.objects.create(
                name=f"{model.name}/{level}",
                creator=ctx.user,
                organization=ctx.organization,
            )

        data_array = models.DataArray.objects.create(
            level=level,
            store=store,
            dataset=dataset,
            coordinate_system=array_system,
            shape=store.shape,
            chunk_shape=store.chunks,
            # Null for level 0: it was not downsampled from anything.
            scale_method=scale_methods.get(level),
        )

        if level == 0:
            continue

        graph_logic.create_pixel_axes(array_system, model.axes)

        graph_logic.create_level_edge(
            array_system=array_system,
            intrinsic=intrinsic,
            shape_0=base_shape,
            shape_level=store.shape,
            axis_specs=axis_specs,
            ctx=ctx,
        )

    if model.derived_from:
        coordinate_system_logic.write_derivation_edges(info, name=dataset.name, own_system=intrinsic, derived_from=model.derived_from, ctx=ctx)

    file_link_logic.write_file_links(info, container=dataset, source_files=model.source_files or [], ctx=ctx)

    for anchor in model.anchors or []:
        coordinate_anchor = models.CoordinateAnchor.objects.create(
            dataset=dataset,
            coordinates={axis_anchor.axis: axis_anchor.value for axis_anchor in anchor.axis_anchors},
        )

        if anchor.microscope:
            # The same write path as the lightpath graph: the typed model's dump IS the
            # stored JSON, so the column never grows a shape the types cannot express.
            models.OptikitState.objects.create(
                anchor=coordinate_anchor,
                state=anchor.microscope.model_dump(),
            )

        if anchor.ome_metadata:
            logger.debug("Creating OME metadata for coordinate anchor with coordinates %s", coordinate_anchor.coordinates)
            models.OmeMetadata.objects.create(
                anchor=coordinate_anchor,
                metadata=_parse_json_object(anchor.ome_metadata.metadata_string, "anchor.omeMetadata.metadataString"),
            )

        if anchor.value_histogram:
            models.ValueHistogram.objects.create(
                anchor=coordinate_anchor,
                histogram=anchor.value_histogram.histogram,
                bins=anchor.value_histogram.bins,
                min=anchor.value_histogram.min,
                max=anchor.value_histogram.max,
                p1=anchor.value_histogram.p1,
                p99=anchor.value_histogram.p99,
            )

        if anchor.label:
            models.ChannelLabel.objects.create(
                anchor=coordinate_anchor,
                label=anchor.label.label,
            )

        if anchor.light_graph:
            # The storage graph's dump, not the input's: `to_graph` builds the union the
            # read side rebuilds, so the column cannot grow a shape the types cannot
            # express -- which is what the comment above claims for every spoke, and what
            # dumping the input model directly quietly broke for this one.
            models.LightPath.objects.create(
                anchor=coordinate_anchor,
                graph=anchor.light_graph.to_graph().model_dump(mode="json"),
            )

        if anchor.phasor_histogram:
            _write_phasor_histogram(coordinate_anchor, anchor.phasor_histogram, axis_specs)

        if anchor.phasor_calibration:
            _write_phasor_calibration(coordinate_anchor, anchor.phasor_calibration, axis_specs)

    return dataset


# ---------------------------------------------------------------------------
# Phasor metadata
#
# Both spokes describe a *phasor*: the DFT of a pixel's profile along one axis at
# one harmonic. Neither the axis nor the harmonic is an array coordinate, so the
# anchor's `coordinates` dict cannot pin them -- which is why both spokes carry
# them as columns, are ForeignKeys rather than the usual OneToOne, and are keyed
# on (anchor, axis, harmonic).
#
# They are attached post-ingest far more often than at ingest: a distribution
# means reading the cube, and a calibration means measuring a reference dye.
# Until now there was no way to attach any metadata to a dataset after ingest at
# all -- anchors were only ever created inside create_array_dataset.
# ---------------------------------------------------------------------------


def _assert_phasor_axis(axis_name: str, axis_specs: list[coords_logic.AxisSpec]) -> None:
    """Check the axis a phasor was taken over is one a DFT means something over.

    The same rule the render node enforces (`core.mutations.layer.assert_phasor_axis`), applied
    at the metadata boundary too: a density stored against the `z` axis is not a phasor, and
    nothing downstream could tell it from one.
    """
    axis = next((spec for spec in axis_specs if spec.name == axis_name), None)
    if axis is None:
        raise ValueError(f"axis '{axis_name}' is not an axis of this dataset ({[spec.name for spec in axis_specs]})")
    if not coords_logic.is_phasor_axis(axis.type):
        raise ValueError(f"axis '{axis_name}' is a {axis.type} axis, not a MICROTIME or SPECTRUM axis. A phasor is only defined over a continuously sampled axis -- an arrival-time histogram or a spectrum.")


def _write_phasor_histogram(anchor: "models.CoordinateAnchor", input: PhasorHistogramInputModel, axis_specs: list[coords_logic.AxisSpec]) -> "models.PhasorHistogram":
    """Create or replace the phasor distribution at (anchor, axis, harmonic)."""
    _assert_phasor_axis(input.axis, axis_specs)

    bins = input.bins if input.bins is not None else 256
    if len(input.counts) != bins * bins:
        raise ValueError(f"counts has {len(input.counts)} entries but bins is {bins}, which needs {bins * bins}. It is the flattened bins x bins density grid, row-major with s outermost.")

    histogram, _ = models.PhasorHistogram.objects.update_or_create(
        anchor=anchor,
        axis=input.axis,
        harmonic=input.harmonic if input.harmonic is not None else 1,
        defaults={
            "bins": bins,
            "counts": input.counts,
            "g_min": input.g_min if input.g_min is not None else 0.0,
            "g_max": input.g_max if input.g_max is not None else 1.0,
            "s_min": input.s_min if input.s_min is not None else 0.0,
            "s_max": input.s_max if input.s_max is not None else 0.6,
            "total": input.total,
            "calibrated": input.calibrated if input.calibrated is not None else False,
            "profile": input.profile or [],
        },
    )
    return histogram


def _write_phasor_calibration(anchor: "models.CoordinateAnchor", input: PhasorCalibrationInputModel, axis_specs: list[coords_logic.AxisSpec]) -> "models.PhasorCalibration":
    """Create or replace the instrument-response correction at (anchor, axis, harmonic)."""
    _assert_phasor_axis(input.axis, axis_specs)

    calibration, _ = models.PhasorCalibration.objects.update_or_create(
        anchor=anchor,
        axis=input.axis,
        harmonic=input.harmonic if input.harmonic is not None else 1,
        defaults={
            "phase_offset": input.phase_offset,
            "modulation_factor": input.modulation_factor,
            "reference": input.reference,
        },
    )
    return calibration


def _get_or_create_anchor(dataset: "models.ArrayDataset", axis_anchors: list[AxisAnchorInputModel] | None) -> "models.CoordinateAnchor":
    """The anchor at these coordinates on this dataset, creating it if it is new.

    Get-or-create rather than create: a phasor distribution and an intensity histogram at the
    same coordinate are two spokes of *one* anchor, and a second anchor at the same coordinates
    would split the metadata of one pixel across two anchors.
    """
    coordinates = {axis_anchor.axis: axis_anchor.value for axis_anchor in axis_anchors or []}
    anchor, _ = models.CoordinateAnchor.objects.get_or_create(dataset=dataset, coordinates=coordinates)
    return anchor


class CreatePhasorHistogramInputModel(PhasorHistogramInputModel):
    dataset: str = Field(description="The ID of the dataset the phasor was computed from")
    axis_anchors: list[AxisAnchorInputModel] | None = Field(None, description="The coordinates the distribution is pinned to")


@kante.pydantic_input(CreatePhasorHistogramInputModel, description="Attach a phasor distribution to a dataset: the 2D (g, s) density of a phasor taken over one axis at one harmonic. Computed after ingest by a task that reads the cube; recomputing at the same harmonic replaces it, while a second harmonic lands beside the first")
class CreatePhasorHistogramInput:
    dataset: strawberry.ID = strawberry.field(description="The ID of the dataset the phasor was computed from")
    axis: str = strawberry.field(description="The axis the phasor was taken over, e.g. 'tau'. Must be a MICROTIME or SPECTRUM axis of the dataset")
    counts: list[float] = strawberry.field(description="The flattened bins x bins (g, s) density, row-major with s outermost")
    axis_anchors: list[AxisAnchorInput] | None = strawberry.field(default=None, description="The coordinates the distribution is pinned to, e.g. [{'axis': 'c', 'value': 0}] for one detection channel. Omit for a distribution global over the dataset")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic the phasor was taken at (default 1)")
    bins: int | None = strawberry.field(default=None, description="The resolution of the square density grid (default 256)")
    g_min: float | None = strawberry.field(default=None, description="The lower g bound of the grid (default 0)")
    g_max: float | None = strawberry.field(default=None, description="The upper g bound of the grid (default 1)")
    s_min: float | None = strawberry.field(default=None, description="The lower s bound of the grid (default 0)")
    s_max: float | None = strawberry.field(default=None, description="The upper s bound of the grid (default 0.6)")
    total: int | None = strawberry.field(default=None, description="The number of pixels that contributed, so counts can be normalized")
    calibrated: bool | None = strawberry.field(default=None, description="Whether the g/s were reference-corrected when computed (default false)")
    profile: list[float] | None = strawberry.field(default=None, description="The summed profile along the phasor axis: a decay for a MICROTIME axis, a spectrum for a SPECTRUM one")


def create_phasor_histogram(info: Info, input: CreatePhasorHistogramInput) -> types.PhasorHistogram:
    """Attach a phasor distribution to a dataset."""
    model = input.to_pydantic()

    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    anchor = _get_or_create_anchor(dataset, model.axis_anchors)
    return _write_phasor_histogram(anchor, model, dataset.axis_specs)


class CreatePhasorCalibrationInputModel(PhasorCalibrationInputModel):
    dataset: str = Field(description="The ID of the dataset the correction applies to")
    axis_anchors: list[AxisAnchorInputModel] | None = Field(None, description="The coordinates the correction is pinned to")


@kante.pydantic_input(CreatePhasorCalibrationInputModel, description="Attach an instrument-response correction to a dataset, taking a raw phasor to a calibrated one. Measured once per detector from a reference acquisition. Its absence is legitimate: an uncalibrated phasor still renders, its hue is just not traceable to an absolute lifetime")
class CreatePhasorCalibrationInput:
    dataset: strawberry.ID = strawberry.field(description="The ID of the dataset the correction applies to")
    axis: str = strawberry.field(description="The axis the correction applies to, e.g. 'tau'. Must be a MICROTIME or SPECTRUM axis of the dataset")
    axis_anchors: list[AxisAnchorInput] | None = strawberry.field(default=None, description="The coordinates the correction is pinned to, e.g. [{'axis': 'c', 'value': 0}] -- the IRF differs per detector. Omit for a correction global over the dataset")
    harmonic: int | None = strawberry.field(default=None, description="The harmonic the correction applies at (default 1)")
    phase_offset: float | None = strawberry.field(default=None, description="The phase correction in radians, added to each pixel's phase")
    modulation_factor: float | None = strawberry.field(default=None, description="The modulation correction, multiplied into each pixel's modulus")
    reference: str | None = strawberry.field(default=None, description="What the correction was measured against, e.g. 'Rhodamine 6G, 4.1 ns'")


def create_phasor_calibration(info: Info, input: CreatePhasorCalibrationInput) -> types.PhasorCalibration:
    """Attach an instrument-response correction to a dataset."""
    model = input.to_pydantic()

    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    anchor = _get_or_create_anchor(dataset, model.axis_anchors)
    return _write_phasor_calibration(anchor, model, dataset.axis_specs)


class UpdateArrayDatasetInputModel(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


@kante.pydantic_input(
    UpdateArrayDatasetInputModel,
    description="Input for renaming or redescribing a dataset. These two fields are the whole of what is editable: the arrays, the axes and the coordinate systems built from them are fixed at creation, and a recomputation is a new dataset",
)
class UpdateArrayDatasetInput:
    """Input for updating a dataset."""

    id: strawberry.ID = strawberry.field(description="The ID of the dataset to update")
    name: str | None = strawberry.field(default=None, description="A new name")
    description: str | None = strawberry.field(default=None, description="A new description")


def update_array_dataset(info: Info, input: UpdateArrayDatasetInput) -> types.ArrayDataset:
    """Rename a dataset, or redescribe it. Those two fields are the whole of what is editable.

    Deliberately not here: the arrays, the axes, and the coordinate systems derived from them.
    The dataset's geometry is not a set of columns to be corrected -- its dimensions live on
    its INTRINSIC system's axes, and ``Axis.order`` is written by enumeration with the rest of
    the graph measured against it, so an axis edit is a *different space*, not a repair of this
    one. ``updateCoordinateSystem`` refuses a dataset's own system for that reason; it serves
    anchors alone. A recomputation is a new dataset.

    Both fields are audited: ``ArrayDataset.provenance`` records a history row per save, attributed
    to the client, user and task the change happened under, and ``ArrayDataset.provenanceEntries``
    reads them back. That is the whole point of routing a rename through a mutation rather than
    leaving the column writable by whatever happens to hold the row.
    """
    model = input.to_pydantic()
    dataset = get_for_org(models.ArrayDataset, info, id=model.id)
    if model.name is not None:
        dataset.name = model.name
    if model.description is not None:
        dataset.description = model.description
    dataset.save()
    return dataset


class SetDefaultSceneInputModel(BaseModel):
    """A dataset and the scene it nominates, or null to clear."""

    dataset: str = Field(description="The dataset to nominate a scene for")
    scene: str | None = Field(default=None, description="The scene to nominate, or null to clear the nomination")


@kante.pydantic_input(SetDefaultSceneInputModel, description="Nominate the scene to open for a dataset, and take its thumbnail from")
class SetDefaultSceneInput:
    """Input for nominating a dataset's default scene."""

    dataset: strawberry.ID = strawberry.field(description="The dataset to nominate a scene for")
    scene: strawberry.ID | None = strawberry.field(default=None, description="The scene to nominate. Null clears the nomination, and the dataset then reports no `latestSnapshot`")


def nominate_default_scene(info: Info, dataset: "models.ArrayDataset", scene: "models.Scene | None") -> "models.ArrayDataset":
    """Write one dataset's nomination, having checked the caller may.

    **Guarded on the dataset, not the scene**, and that is not a detail: `Scene` carries no
    `creator` column at all -- which is why `delete_scene` passes `owner=None` -- so a check on
    the scene has nothing to read. The dataset does carry one, and the dataset is what this
    writes. Without the check any member of the organization could repoint any dataset's
    thumbnail.

    Org scoping is the caller's job (both ids arrive through `get_for_org`); this is the
    ownership half, which scoping does not cover.
    """
    assert_can_delete(info, dataset, self_owner)
    dataset.default_scene = scene
    dataset.save(update_fields=["default_scene"])
    return dataset


def set_default_scene(info: Info, input: SetDefaultSceneInput) -> types.ArrayDataset:
    """Nominate the scene to open for a dataset, or clear the nomination.

    No check that the scene actually shows the dataset. Two dataset-to-scene relations already
    exist and disagree -- layer-based (`ArrayDataset.scenes`) and the anchor-based rule this field
    replaced -- so validating against either would silently pick one as authoritative and make
    the nomination a claim about placement, which is exactly what it must not be.
    """
    model = input.to_pydantic()
    dataset = get_for_org(models.ArrayDataset, info, id=model.dataset)
    scene = get_for_org(models.Scene, info, id=model.scene) if model.scene else None
    return nominate_default_scene(info, dataset, scene)


class DeleteArrayDatasetInputModel(BaseModel):
    id: str = Field(description="The ID of the array dataset to delete")


@kante.pydantic_input(DeleteArrayDatasetInputModel, description="Input for deleting an array dataset by ID")
class DeleteArrayDatasetInput:
    """Input for deleting an array dataset by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the array dataset to delete")


class DeleteDataArrayInputModel(BaseModel):
    id: str = Field(description="The ID of the data array to delete")


@kante.pydantic_input(DeleteDataArrayInputModel, description="Input for deleting a data array by ID")
class DeleteDataArrayInput:
    """Input for deleting a data array by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the data array to delete")


delete_array_dataset = make_delete(models.ArrayDataset, DeleteArrayDatasetInput, owner=self_owner)
delete_data_array = make_delete(models.DataArray, DeleteDataArrayInput, owner=dataset_owner)
