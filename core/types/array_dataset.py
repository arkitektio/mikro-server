import datetime

from django.db.models import Prefetch

import strawberry
from strawberry import auto
from typing import TYPE_CHECKING, Annotated, List, Optional
from core import models, scalars, filters, enums
from kante.types import Info
from lightpath.objects.types import LightpathGraph
from optikit.models import OptikitStateModel
from optikit.types import OptikitStateGraph
from lightpath.objects.models import LightpathGraphModel
from core.render.layer.types import LabelColorBy, LabelFilterBy, LabelRender, LayerRenderGraph, MeshColorBy, MeshFilterBy, NetworkColorBy, NetworkFilterBy, PhasorRender
from core.render.layer.label import LabelRenderModel
from core.render import color_by as color_by_models
from core.render import filter_by as filter_by_models
from core.render.layer.models import LayerRenderGraphModel, PhasorRenderModel
from core.render.camera.types import CameraState
from core.render.camera.models import CameraStateModel
from core.inputs.coords import CoordinateInput, at_map
from core.types.visualization import Visualization
from core.types.coords import (
    AffinePlacement,
    CoordinateSystem,
    MeshCollection,
    NetworkCollection,
    PlacementStep,
    Resident,
    Transformation,
)
import kante
from strawberry.scalars import JSON
from datalayer.types import MediaStore, ZarrStore
from core.types._shared import DESCRIPTORS_DESCRIPTION, resolve_descriptors, apply_link_filters, build_prescoped_queryset, OrgScoped
from core.type_gen import create_stats_type

from kanne_server import scalars as kanne_scalars

from core import descriptors as descriptors_logic
from core import order, base_models
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import file_link as file_link_logic
from core.logic import graph as graph_logic
from core.logic import phasor as phasor_logic
from core.logic import scene_graph

from core.types.auth import ProvenanceEntry, Task, User
from embeddings.strawberry import Embedding, embedding_of

if TYPE_CHECKING:
    # Only for the lazy annotation on the file-link fields below; importing it at runtime
    # would be a cycle, since `core.types.file_link` references this module in return.
    from core.types.file_link import FileLink

    # Same reason: `core.types.folder` imports this module for the layer/scene types.
    from core.types.folder import Folder


#: Key for the per-request latest-snapshot-per-scene map on the context's loader store.
_LATEST_SNAPSHOT_KEY = "latest_snapshot_by_scene"


# Where `ArrayDataset.fullLens` finds its prefetched answer: the dataset's unsliced lenses,
# oldest first. Its own attribute rather than the `lenses` relation, so the prefetch cannot
# narrow what `ArrayDataset.lenses` reads on the same row.
_FULL_LENSES_ATTR = "_prefetched_full_lenses"
#: The whole lens with its nomination, for the dataset's `defaultScene`/`latestSnapshot`: a second prefetch of the same row, under its own name so the two hints never collide.
_NOMINATING_LENS_ATTR = "_prefetched_nominating_lens"
_NOMINATING_LENS_PREFETCH = lambda info: Prefetch("lenses", queryset=models.ArrayLens.objects.filter(slices=[]).select_related("default_scene").order_by("pk"), to_attr=_NOMINATING_LENS_ATTR)  # noqa: E731


def _nominating_lens(dataset) -> "models.ArrayLens | None":  # noqa: ANN001 - the ArrayDataset row
    """The dataset's whole lens, which holds its nomination; prefetched on a page, one query otherwise."""
    prefetched = getattr(dataset, _NOMINATING_LENS_ATTR, None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return models.ArrayLens.objects.filter(dataset=dataset, slices=[]).select_related("default_scene").order_by("pk").first()


def _latest_snapshot_map(info: Info) -> "dict[int, models.SceneSnapshot] | None":
    """The latest snapshot per scene id, organization-scoped, built once per request.

    One ``DISTINCT ON (scene_id)`` query (Postgres-specific) covers every scene the
    request can see, so a page of scenes or datasets reads its tiles from a dict
    instead of running one ``ORDER BY created_at LIMIT 1`` per row.

    Still whole-org rather than scoped to the ids on the page, and deliberately: the map is
    built lazily on the *first* row that asks, and a resolver has no view of the other rows'
    scenes at that moment. Scoping it would need the page's scene ids up front, which is a
    prefetch-shaped change rather than a filter. The index it rides
    (``scene_snapshot_latest_idx`` on ``(scene, -created_at)``) makes the scan cheap, and the
    map holds at most one row per scene.

    Returns None when the context carries no loader store (off-request use) --
    callers then fall back to their per-row query rather than paying a whole-org
    scan with nowhere to cache it.
    """
    loaders = getattr(info.context, "_loaders", None)
    if loaders is None:
        return None
    by_scene = loaders.get(_LATEST_SNAPSHOT_KEY)
    if by_scene is None:
        rows = (
            models.SceneSnapshot.objects.filter(organization=info.context.request.organization)
            .select_related("store")
            .order_by("scene_id", "-created_at", "-pk")
            .distinct("scene_id")
        )
        by_scene = {snap.scene_id: snap for snap in rows}
        loaders[_LATEST_SNAPSHOT_KEY] = by_scene
    return by_scene


def _default_scene_snapshot(info: Info, dataset) -> "SceneSnapshot | None":
    """The newest picture of the scene this dataset nominates, or None if it nominates none.

    This replaced a walk. ``latestSnapshot`` used to derive its answer from *sole occupancy* --
    the newest picture of a scene whose only anchored dataset was this one -- which cost five
    whole-org queries to build a map and then declined to answer at all whenever the dataset
    shared a scene with any other. A gallery of datasets staged together, the common case, got
    no tiles for six queries a page.

    A nominated scene is both cheaper and more honest: ``default_scene_id`` is a local column,
    so the whole map is gone, and the answer is one a person chose rather than one inferred
    from an arrangement that may not even be drawn.

    **What it gives up**: the old answer guaranteed the picture showed this dataset and nothing
    else. A nominated scene may blend several. That is the trade, and the field description
    says so.
    """
    whole = _nominating_lens(dataset)
    return _scene_snapshot(info, whole.default_scene_id if whole is not None else None)


def _scene_snapshot(info: Info, scene_id: "int | None") -> "SceneSnapshot | None":
    """The newest picture of one scene, read from the per-request map when there is one.

    The lookup half of :func:`_default_scene_snapshot`, split out because a lens answers the
    same question with a scene id of its own -- its nomination when it is sliced -- and must
    read the same map rather than run a query per card.
    """
    if scene_id is None:
        return None
    by_scene = _latest_snapshot_map(info)
    if by_scene is None:
        return models.SceneSnapshot.objects.filter(scene_id=scene_id).order_by("-created_at", "-pk").first()
    return by_scene.get(scene_id)


@kante.django_type(
    models.ArrayDataset,
    filters=filters.ArrayDatasetFilter,
    ordering=order.ArrayDatasetOrder,
    pagination=True,
    description="A multi-dimensional array dataset. Its dimensions and their types live on the axes of its INTRINSIC (pixel grid) coordinate system; physical units live on the physical spaces it has edges into; its pyramid levels are DataArrays, each mapping into its grid",
)
class ArrayDataset(OrgScoped):
    """A multi-dimensional array dataset with named dimensions, described by its intrinsic pixel-grid coordinate system."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    @kante.django_field(description="This dataset's stored vector, as `<model id>:<floats>`. Null until it has been indexed.")
    def embedding(self) -> Embedding | None:
        return embedding_of(self)

    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(
        description="The folder this dataset is filed in. Organisational only: it says where a user keeps this dataset, never where the data sits in space -- that is `intrinsicSystem` and the edges out of it"
    )

    @kante.django_field(
        description=(
            "The files this dataset was converted from -- the CZI a converter read to write these arrays, named per series. **Read this alongside `derivedFrom`, not instead of "
            "it**: `derivedFrom` says which *data* this was computed from and relates two coordinate systems, while this says which *bytes* it was read out of and relates to no "
            "space at all, because a file has none. Both can be non-empty and complete"
        ),
        prefetch_related=["file_links__file"],
    )
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this dataset was produced from."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(
        description="The files written out of this dataset: an OME-TIFF export, a rendered snapshot registered as a file. The mirror of `sourceFiles`",
        prefetch_related=["file_links__file"],
    )
    def exports(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file written out of this dataset."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.RENDITION), filters, info)

    id: auto
    name: auto
    description: str | None
    # `name` and `description` are the only two fields `updateArrayDataset` can reach, which is
    # exactly why the audit trail is worth reading: a rename is the one thing about a dataset
    # that can change, so who changed it is the one history there is to keep.
    provenance_entries: List["ProvenanceEntry"] = kante.django_field(
        description="Every change made to this dataset: who created it, and every subsequent rename or redescription, attributed to the client, user and task it happened under. Only `name` and `description` can change -- the arrays, the axes and the coordinate systems built from them are fixed at creation"
    )
    created_through: Task | None = kante.django_field(description="The task this dataset was created through, if any")
    anchors: List["CoordinateAnchor"] = kante.django_field(
        description="The coordinate anchors of this dataset, each pinning metadata spokes -- a microscope state, OME metadata, a value histogram, a channel label -- to some of its level-0 coordinates"
    )
    created_through_by: User | None = kante.django_field(description="The assigner of the creating task, if any")
    data_arrays: List["DataArray"] = kante.django_field(description="The multiscale data arrays belonging to this dataset")

    @kante.django_field(description="The dataset's INTRINSIC coordinate system: its level-0 pixel grid, the space every pyramid level and lens maps into and the space ROIs resolve against. Structural and unit-independent")
    def intrinsic_system(self, info: Info) -> CoordinateSystem | None:
        """The dataset's INTRINSIC coordinate system."""
        return self.intrinsic_coordinate_system

    @kante.django_field(
        description="The edges from this dataset's pixel grid back into the lenses it was computed from, when it is a derived dataset: one for a deconvolution or a resample, several for a fusion of channels or tiles. Empty for a dataset that was acquired rather than derived. The order is the priority its creator declared: the first edge is the primary parent, the one that places the dataset. They are edges, not labels: each carries the map itself, so a client can compose it -- and they are why a derived dataset inherits its sources' placements instead of needing its own registration"
    )
    def derived_from(self, info: Info) -> List[Transformation]:
        """The stored derivation edges, primary parent first, if this dataset was computed from others."""
        return graph_logic.derivation_edges(self)

    @kante.django_field(
        description="The datasets computed from this one -- the other end of `derivedFrom`, and the way to ask what a source produced: the deconvolutions, segmentations and projections that named a space of this dataset as their parent. Derived from the same edges, never a stored back-reference that could disagree with them. Every child, not just those this dataset places: a fusion that named this source second is listed here, and so is a child whose derivation is UNMAPPABLE -- it came from here even though its geometry did not survive. The maps themselves are on each child's own `derivedFrom`"
    )
    def derived_datasets(self, info: Info) -> List["ArrayDataset"]:
        """The datasets whose derivation edges land in one of this dataset's spaces."""
        return graph_logic.derived_datasets(self)

    @kante.django_field(
        description=(
            "Everything computed from this dataset, whatever kind of container it is: the derived datasets `derivedDatasets` lists, and also the measurement tables, mesh "
            "collections and annotation collections that named this dataset as their source. A separate field rather than a widening of that one, which stays honestly about "
            "*datasets*. Same edges, same kind-blindness: an UNMAPPABLE child came from here even though its geometry did not survive"
        )
    )
    def derived_residents(self, info: Info) -> List[Resident]:
        """Every container whose derivation edges land in one of this dataset's spaces."""
        return graph_logic.derived_containers(self)

    @kante.django_field(description="Whether this dataset carries a resolution pyramid. Derived: true when it has more than one level")
    def multiscale(self, info: Info) -> bool:
        """Whether the dataset has more than one pyramid level."""
        return self.multiscale

    @kante.django_field(description="The dataset's axis names, in array order. Derived from the axes of its intrinsic coordinate system")
    def axis_names(self, info: Info) -> List[str]:
        """The dataset's axis names."""
        return self.axis_names

    @kante.django_field(
        prefetch_related=["data_arrays"],
        description=(
            "Whether every downsampled level of this pyramid was built by a method that only ever returns a value already present in the input -- NEAREST or MODE. Only meaningful "
            "when the values are object ids, and only *reportable* rather than enforceable: `createArrayDataset` refuses a non-compliant pyramid on a dataset already declared "
            "CATEGORIZED or carrying an INDEX axis, but a mask can be declared a mask afterwards, by the `keyedBy` FIELD edge authored when its object table is created -- and by then the levels exist. "
            "False means the levels above 0 hold ids that were interpolated into existence and belong to no object; treat level 0 as the only trustworthy one. Null when no level "
            "says how it was made, which is not the same as compliant. True for an unpyramided dataset: there is nothing that could be wrong"
        ),
    )
    def pyramid_is_label_compliant(self, info: Info) -> bool | None:
        """Whether every downsampled level was built by picking rather than averaging."""
        methods = [array.scale_method for array in self.data_arrays.all() if array.level != 0]
        if not methods:
            return True
        if all(method is None for method in methods):
            return None
        return all(method in enums.LABEL_COMPLIANT_SCALE_METHODS for method in methods)

    @kante.django_field(
        description="What this dataset structurally is, materialized at creation from the axes of its intrinsic coordinate system and its level-0 shape: the one spatial spec its SPACE axes denote, then a modifier per non-spatial axis type: the acquisition ones (TIMESERIES, MULTICHANNEL, SPECTRAL, FLIM), then the ones that say what the values or positions are (DISPLACEMENT_FIELD, COORDINATE_FIELD, INDEXED). An axis counts only when it has more than one position. A 3D timelapse is [VOLUME, TIMESERIES, MULTICHANNEL]; a 2D flow field is [IMAGE, DISPLACEMENT_FIELD]. Last comes TRACE, for a dataset that is a curve along one measured axis: a decay is [SCALAR, FLIM, TRACE], a line profile per channel [PROFILE, MULTICHANNEL, TRACE]. A stack stored with a single plane is an IMAGE, and a single frame is not a TIMESERIES; `hasAxisTypes` is the filter for whether an axis is declared at all. Empty while the intrinsic system does not exist yet"
    )
    def spec(self, info: Info) -> List[enums.ArrayDatasetSpec]:
        """Every spec the dataset's axes satisfy."""
        return self.spec

    @kante.django_field(description="The dataset's shape: that of its level-0 array")
    def shape(self, info: Info) -> List[int]:
        """The dataset's shape."""
        return self.shape_list

    @kante.django_field(
        description="The scenes this dataset is rendered in, reached through its lenses' layers. Derived, never stored: a scene is a composition and this is a fact of the graph, so there is no dataset-to-scene column that could disagree with it. A scene shows this dataset once a layer over one of its lenses is composed there -- typically by staging one of its spaces with createSceneFromCoordinateSystem"
    )
    def scenes(self, info: Info) -> List["Scene"]:
        """The scenes this dataset is rendered in, through its lenses' layers."""
        return list(models.Scene.objects.filter(layers__lens__arraylens__dataset=self).distinct())

    @kante.django_field(
        description=(
            "The array lenses over this dataset, oldest first. `sliced: true` keeps the ones that cut something out -- the crops and sub-volumes people work on; `sliced: false` the one that "
            "selects everything, which is `fullLens`. A list for people wants `sliced: true` and `fullLens` beside it"
        )
    )
    def lenses(self, info: Info, sliced: Optional[bool] = None) -> List["ArrayLens"]:
        """The dataset's lenses, optionally only the sliced or only the unsliced ones."""
        lenses = self.lenses.order_by("pk")
        if sliced is None:
            return list(lenses)
        return list(lenses.exclude(slices=[]) if sliced else lenses.filter(slices=[]))

    @kante.django_field(
        # One query for a page of datasets rather than one a row: every tile links to this.
        prefetch_related=[lambda info: Prefetch("lenses", queryset=models.ArrayLens.objects.filter(slices=[]).order_by("pk"), to_attr=_FULL_LENSES_ATTR)],
        description=(
            "The lens that selects this whole dataset: what a viewer opens for it, and the handle to pass wherever a `Lens` is asked for and the whole array is meant. Every dataset is "
            "created with one, and there is one; null only for a dataset with no pixel grid to select from -- reading never mints one"
        ),
    )
    def full_lens(self, info: Info) -> Optional["ArrayLens"]:
        """The whole lens over this dataset, if any."""
        prefetched = getattr(self, _FULL_LENSES_ATTR, None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return self.lenses.filter(slices=[]).order_by("pk").first()

    @kante.django_field(
        prefetch_related=[_NOMINATING_LENS_PREFETCH],
        description=(
            "The scene to open for this dataset, and where `latestSnapshot` comes from: the nomination its whole lens holds (`fullLens.defaultScene`), so `setDefaultScene` here and "
            "`setLensDefaultScene` on that lens are one write. A nomination, not a derivation: it says nothing about where the data sits and is **not** the answer to which scenes show "
            "this dataset -- that is `scenes`, which the coordinate graph answers and which no stored column could contradict. Null until something sets it, with `setDefaultScene`, "
            "`setLensDefaultScene`, or `defaultFor` on either scene-creating mutation"
        ),
    )
    def default_scene(self, info: Info) -> Optional["Scene"]:
        """The scene this dataset nominates, through its whole lens."""
        whole = _nominating_lens(self)
        return whole.default_scene if whole is not None else None

    @kante.django_field(
        prefetch_related=[_NOMINATING_LENS_PREFETCH],
        description=(
            "The most recent picture of this dataset's `defaultScene`, for previewing it without loading the array. Null when no default scene is set. **A picture of the scene, not "
            "of the dataset**: snapshots are taken of compositions, so if the nominated scene stages other data too, the tile shows that data as well. This used to answer instead "
            "from *sole occupancy* -- the newest picture of a scene whose only anchored dataset was this one -- which guaranteed the picture showed nothing else but returned null "
            "for every dataset staged alongside another, and cost a five-query graph walk per request to decide"
        )
    )
    def latest_snapshot(self, info: Info) -> Optional["SceneSnapshot"]:
        """The newest picture of the scene this dataset nominates."""
        return _default_scene_snapshot(info, self)


@kante.django_type(
    models.DataArray,
    filters=filters.DataArrayFilter,
    ordering=order.DataArrayOrder,
    pagination=True,
    description="One level of a dataset's resolution pyramid: a zarr-backed array, with its own voxel-index coordinate system and a stored edge into the dataset's intrinsic space",
)
class DataArray(OrgScoped):
    """One level of a dataset's resolution pyramid, with the edge that places it in the dataset's intrinsic space."""

    id: auto
    store: ZarrStore
    shape: list[int]
    chunk_shape: list[int]
    level: int
    scale_method: enums.ScaleMethod | None = strawberry.field(
        description="How this level's voxels were computed from the level above it. Null for level 0, which was downsampled from nothing, and null for a level whose writer did not say. Over a dataset whose values are object ids only NEAREST and MODE are honest -- see `ArrayDataset.pyramidIsLabelCompliant`"
    )

    @kante.django_field(
        select_related=["coordinate_system", "dataset__coordinate_system"],
        description="The coordinate system this level's voxels live in. Level 0 owns none: the dataset's INTRINSIC system IS the level-0 pixel grid, so this resolves to it. Higher levels own an ARRAY (voxel index) system",
    )
    def coordinate_system(self, info: Info) -> CoordinateSystem | None:
        """The system this level's voxels live in: its own ARRAY system, or intrinsic for level 0."""
        return self.space

    @kante.django_field(
        description="The edge from this level's voxel space into the dataset's intrinsic space. Its scale is absolute -- derived from the actual shapes, not from a nominal 2**level -- so a pyramid whose axes do not halve cleanly is described correctly. Null for level 0: its space IS the intrinsic space, and there is nothing to map",
    )
    def to_parent(self, info: Info) -> Transformation | None:
        """The stored level-to-intrinsic edge."""
        return self.to_parent


@kante.django_type(
    models.OptikitState,
    pagination=True,
    description="The hardware truth: the recorded microscope (Optikit) state pinned to a coordinate anchor",
)
class OptikitState:
    """The hardware truth: the recorded microscope (Optikit) state pinned to a coordinate anchor"""

    id: auto

    @kante.django_field(description="The recorded microscope state, reconstructed into its typed form: stage pose and environment as quantities, everything else as per-device named settings")
    def state(self, info: Info) -> OptikitStateGraph:
        """The recorded microscope state."""
        return OptikitStateModel(**self.state)


@kante.django_type(
    models.ValueHistogram,
    pagination=True,
    description="The distribution of pixel values pinned to a coordinate anchor, including histogram bins, min/max and percentile limits",
)
class ValueHistogram:
    """The distribution of pixel values pinned to a coordinate anchor, including histogram bins, min/max and percentile limits"""

    id: auto
    p1: float | None
    p99: float | None
    histogram: list[float]
    bins: list[float]
    min: float | None
    max: float | None


@kante.django_type(
    models.LightPath,
    pagination=True,
    description="The light path truth: the optical light path graph pinned to a coordinate anchor",
)
class LightPath:
    """The light path truth: the optical light path graph pinned to a coordinate anchor"""

    id: auto

    @kante.django_field()
    def graph(self, info: Info) -> LightpathGraph:
        return LightpathGraphModel(**self.graph)


@kante.django_type(
    models.ChannelLabel,
    pagination=True,
    description="The channel truth: a human-readable label for a channel, pinned to a coordinate anchor",
)
class ChannelLabel:
    """The channel truth: a human-readable label for a channel, pinned to a coordinate anchor"""

    id: auto
    label: str


@kante.django_type(
    models.PhasorHistogram,
    pagination=True,
    description="The distribution of a phasor pinned to a coordinate anchor: a 2D (g, s) density plus the summed profile it was computed from. What ValueHistogram is to an intensity channel -- it lets a client pick a sane value range for a phasor overlay without reading the cube",
)
class PhasorHistogram:
    """The distribution of a phasor pinned to a coordinate anchor."""

    id: auto
    axis: str
    harmonic: int
    bins: int
    g_min: float
    g_max: float
    s_min: float
    s_max: float
    total: int | None
    calibrated: bool

    @kante.django_field(description="The flattened bins x bins (g, s) density, row-major with s outermost")
    def counts(self, info: Info) -> list[float]:
        return self.counts

    @kante.django_field(description="The summed profile along the phasor axis (a decay for a MICROTIME axis, a spectrum for a SPECTRUM one), one value per bin")
    def profile(self, info: Info) -> list[float]:
        return self.profile


@kante.django_type(
    models.PhasorCalibration,
    pagination=True,
    description="The instrument-response correction taking a raw phasor to a calibrated one, pinned to a coordinate anchor. An acquisition fact, not a display choice: two layers over one dataset cannot coherently disagree about it. Its absence means the phasor is uncalibrated, which still renders",
)
class PhasorCalibration:
    """The instrument-response correction taking a raw phasor to a calibrated one."""

    id: auto
    axis: str
    harmonic: int
    phase_offset: float | None
    modulation_factor: float | None
    reference: str | None


@kante.django_type(
    models.CoordinateAnchor,
    filters=filters.CoordinateAnchorFilter,
    pagination=True,
    description=(
        "The axis-agnostic hub that pins metadata spokes (microscope state, OME metadata, value histograms, channel labels, light paths, phasor distributions and calibrations) to "
        "specific coordinates of an array, table or sparse dataset. Exactly one of `dataset`, `table` and `sparse` is set"
    ),
)
class CoordinateAnchor:
    """The axis-agnostic hub that pins metadata spokes to specific coordinates of an array, table or sparse dataset."""

    id: auto
    dataset: Optional[ArrayDataset] = kante.django_field(description="The array dataset this anchor pins into, or null otherwise")
    table: Optional[Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")]] = kante.django_field(description="The table dataset this anchor pins into, or null otherwise")
    sparse: Optional[Annotated["SparseDataset", strawberry.lazy("core.types.sparse_dataset")]] = kante.django_field(description="The sparse dataset this anchor pins into, or null otherwise")
    # The reverse accessor from OptikitState.anchor is `microscope`, not `optikit_state`.
    microscope: OptikitState | None = kante.django_field(description="The microscope state recorded at this coordinate")
    value_histogram: ValueHistogram | None
    channel_label: ChannelLabel | None
    light_graph: LightPath | None
    # Declared below this type, hence the forward reference.
    ome_metadata: Optional["OmeMetadata"] = kante.django_field(description="The OME image metadata recorded at this coordinate")
    # Lists, not single spokes: one anchor may carry a phasor at several harmonics, and over
    # several axes -- neither of which the anchor's coordinates can pin.
    phasor_histograms: list[PhasorHistogram]
    phasor_calibrations: list[PhasorCalibration]

    @kante.django_field(
        description=(
            "The coordinates this anchor is pinned to, e.g. {'c': 0, 't': 5}. For an array dataset these are level-0 pixel indices, i.e. coordinates of its INTRINSIC system; for a "
            "table dataset they are values of its coordinate columns, keyed by column name; for a sparse dataset they are positions along its enumerated axes. An anchor that omits an axis is global along it"
        )
    )
    def coordinates(self, info: Info) -> scalars.Any:
        """The coordinates this anchor is pinned to."""
        return self.coordinates

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the list to the request's organization, through the anchor's own denormalised column."""
        return build_prescoped_queryset(info, queryset)


@kante.django_type(
    models.OmeMetadata,
    pagination=True,
    description="The image truth: OME image metadata pinned to a coordinate anchor",
)
class OmeMetadata:
    """The image truth: OME image metadata pinned to a coordinate anchor"""

    id: auto

    @kante.django_field(description="The OME image metadata")
    def metadata(self, info: Info) -> scalars.Any:
        """The OME image metadata."""
        return self.metadata


@kante.django_type(
    models.Scene,
    filters=filters.SceneFilter,
    pagination=True,
    ordering=order.SceneOrder,
    description="A composition of layers over a shared world coordinate system. The scene carries no units of its own -- they are per-axis, on the axes of its world system",
)
class Scene(OrgScoped, Visualization):
    """A composition of layers over a shared world coordinate system."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    id: auto
    name: auto
    layers: List["Layer"] = kante.django_field(
        filters=filters.LayerFilter,
        ordering=order.LayerOrder,
        pagination=True,
        description="The layers placed in this scene (a heterogeneous list of layer kinds)",
    )
    snapshots: List["SceneSnapshot"] = kante.django_field(
        filters=filters.SceneSnapshotFilter,
        ordering=order.SceneSnapshotOrder,
        pagination=True,
        description="The pre-rendered pictures of this composition, for previewing it without compositing the layers",
    )
    animations: List["Animation"] = kante.django_field(
        filters=filters.AnimationFilter,
        ordering=order.AnimationOrder,
        pagination=True,
        description="The named camera tours through this composition",
    )
    preferred_view: enums.PreferredView = kante.django_field(description="How a viewer should open this scene: flat, volumetric, or its own choice. A preference, not a constraint -- nothing server-side reads it, and a viewer that cannot render volumes is not wrong to show the slice view")
    background_color: list[float] | None = kante.django_field(description="The viewer background, as RGBA. Null lets the viewer use its own")
    @kante.django_field(
        description=(
            "The lenses that nominate this scene as the one to open for them, and take their thumbnail from it -- a container's whole lens where the container was nominated for. Several "
            "may: a scene staging a plate is a reasonable landing place for every well in it. Not the lenses this scene *shows* -- for that, ask each lens' `scenes`, which the "
            "coordinate graph derives"
        )
    )
    def default_for(self, info: Info) -> List["Lens"]:
        """The lenses nominating this scene."""
        return list(self.default_for_lenses.all())
    @kante.django_field(description="The most recent picture of this composition -- the tile to put on the scene. Null until something snapshots it")
    def latest_snapshot(self, info: Info) -> Optional["SceneSnapshot"]:
        """The newest picture of this scene."""
        by_scene = _latest_snapshot_map(info)
        if by_scene is None:
            return self.snapshots.order_by("-created_at", "-pk").first()
        return by_scene.get(self.pk)

    world_coordinate_system: CoordinateSystem = kante.django_field(
        field_name="world",
        description="The shared space this scene composes its layers over. Never owned by the scene: many scenes can share it, it outlives each of them, and deleting a scene never deletes it",
    )
    # There is deliberately no `coordinateSystems` and no `annotations` here. Both used to
    # answer from the scene's reachable-system set, and that set is a property of the world
    # -- every scene over one world had the same answer, which is the same argument that
    # moved `registrations` off this type. They now hang off the space, as `placedSystems`
    # and `annotations`: ask `worldCoordinateSystem { ... }`.

    @kante.django_field(description="The annotation collection minted for this scene as its default drawing surface, or null before the first annotation is drawn on it. Bookkeeping, not placement: what places the collection is its registration edge into this scene's world")
    def annotation_collection(self, info: Info) -> Optional["AnnotationCollection"]:
        """The scene's minted drawing surface, if any."""
        return getattr(self, "annotation_collection", None)


@kante.pydantic_type(base_models.SliceModel, description="A slice along a named axis, with optional start, stop and step")
class Slice:
    """A slice along a named axis, with optional start, stop and step"""

    axis: str
    start: int | None
    stop: int | None
    step: int | None


@kante.pydantic_type(base_models.WindowModel, description="A value window along a named axis of a container that is not an array: the positions kept, by value, with an open side as null")
class Window:
    axis: str
    min: float | None
    max: float | None


_WINDOW_FILTERS_DESCRIPTION = (
    "The lens' windows as row filters, always applied: a row is drawn when every one keeps it (`min <= column <= max`, an omitted side open), on top of whatever `filterBys` are active. "
    "The selection the layer draws, derived from the lens and never stored; empty for a whole lens"
)
_CLIP_DESCRIPTION = "The lens' windows as a clip box in the collection's own space: geometry outside it is not drawn. The selection the layer draws, derived from the lens; empty for a whole lens"

_LENS_SYSTEM_DESCRIPTION = (
    "The coordinate system the lens' selection is expressed in. A sliced array lens owns one (the space its slices cut out, with the derived edge recording the shift); "
    "every other lens selects within its container's own space and resolves to it"
)


@kante.django_interface(
    models.Lens,
    description=(
        "A selection over a container that lives in a coordinate system: one row per (container, selection), so the same selection asked for twice is the same id. "
        "An ArrayLens selects over an array dataset by index slices and, when sliced, owns the crop's space and the edge back into the dataset's grid; every other kind "
        "selects over its container by value windows in the container's own axes and lives in the container's space. Every container has its whole lens from creation."
    ),
)
class Lens:
    """A selection over a container. The concrete kind says what the container is and how it is selected over."""

    id: auto
    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the heterogeneous list to the request's organization, and load each row's container chain in a batch."""
        scoped = build_prescoped_queryset(info, queryset)
        return scoped.with_sources() if hasattr(scoped, "with_sources") else scoped

    @kante.django_field(description="What this lens selects over: an array by slices, or a table, a sparse dataset, a mesh, a network or an annotation collection by windows")
    def kind(self, info: Info) -> enums.LensKind:
        """The container kind, from the row's concrete model."""
        return enums.LensKind(type(self).KIND)

    @kante.django_field(select_related=["coordinate_system"], description=_LENS_SYSTEM_DESCRIPTION)
    def coordinate_system(self, info: Info) -> CoordinateSystem | None:
        """The system the lens' selection is expressed in: its own, or its container's."""
        return self.coordinate_system

    name: str | None = kante.django_field(
        description="What someone called this selection, or null when nobody has. A label only: it is no part of what the lens selects, and a client shows the selection -- or 'the whole container' -- in its place"
    )
    created_at: datetime.datetime = kante.django_field(description="When this selection was first asked for; a whole lens, when its container was made")
    creator: User | None = kante.django_field(
        description="Who made this selection: the first to ask for it. The container's creator for a whole lens, which exists from the container's creation. Asking for a selection that exists hands back the row and changes nothing"
    )
    created_through: Task | None = kante.django_field(description="The task this selection was made under, if any")
    created_through_by: User | None = kante.django_field(description="The assigner of the creating task, if any")

    @kante.django_field(
        description=(
            "The scene to open for this lens: its own nomination (`setLensDefaultScene`, or `createSceneFromLens`), falling back to its container's whole lens' while it has none. A "
            "container's nomination is its whole lens', so a whole lens answers for the container. A nomination, not a derivation"
        ),
    )
    def default_scene(self, info: Info) -> Optional["Scene"]:
        """The scene this lens nominates: its own when it selects something and has one, otherwise its container's."""
        scene_id = self.nominated_scene_id
        return models.Scene.objects.filter(pk=scene_id).first() if scene_id is not None else None

    @kante.django_field(description="The coordinate anchors inside this selection: an anchor global along an axis, or pinned inside the lens' range on it. Empty for a container kind that carries no anchors (meshes, networks, annotation collections)")
    def active_anchors(self, info: Info) -> List[CoordinateAnchor]:
        """The anchors the selection keeps."""
        return list(self.active_anchors)

    @kante.django_field(
        description=(
            "The scenes this lens is rendered in: those with a layer drawing through it or through any selection within it, of any kind. Derived, never stored, exactly as "
            "`ArrayDataset.scenes` is. A layer over one channel of a crop draws the crop, and a layer over any crop draws the whole lens -- so a whole lens answers for the whole "
            "container and a window or a crop for what it contains; a scene showing a selection that reaches outside this one is that selection's to report"
        )
    )
    def scenes(self, info: Info) -> List["Scene"]:
        """The scenes with a layer over this lens or a selection within it, whatever its kind."""
        return list(models.Scene.objects.filter(layers__lens_id__in=coordinate_system_logic.lens_ids_within(self)).distinct())


@kante.django_type(
    models.ArrayLens,
    filters=filters.LensFilter,
    ordering=order.LensOrder,
    pagination=True,
    description="A selection over an array dataset by index slices: what an action that works on pixels is handed. Its shape and axes are derived from the dataset and the slices",
)
class ArrayLens(Lens, OrgScoped):
    """A selection over an array dataset. Its shape and axes are derived from the dataset and the slices."""

    id: auto
    dataset: ArrayDataset

    @kante.django_field(
        description="What this selection structurally is: its dataset's `spec` vocabulary, read over the shape its slices cut out. Materialized when the lens is made -- a lens and its dataset are immutable -- and what the `arraySpec` filter reads. An axis counts only when the lens keeps more than one position along it, so one plane of a [VOLUME, MULTICHANNEL] stack is [IMAGE, MULTICHANNEL], one channel of that is [IMAGE], and one row of an image is [PROFILE, TRACE]. The whole lens answers what its dataset does"
    )
    def spec(self, info: Info) -> List[enums.ArrayDatasetSpec]:
        """Every spec the lens' axes satisfy at the shape it selects."""
        return self.spec

    @kante.django_field(
        select_related=["dataset"],
        description=(
            "The most recent picture of this lens' `defaultScene` -- the tile to put on this lens. A lens that nominates a scene shows that one; a crop that nominates nothing shows its "
            "dataset's (its whole lens'), so a crop nobody has staged yet still has a picture. Null when neither nominates a scene"
        ),
    )
    def latest_snapshot(self, info: Info) -> Optional["SceneSnapshot"]:
        """The newest picture of the scene this lens nominates, falling back to its dataset's."""
        return _scene_snapshot(info, self.nominated_scene_id)


    @kante.django_field(
        description=(
            "What a layer over this lens would answer for `renderAxes`, before any layer exists: which of the lens' axes face screen x, y, z, time and intensity under the default convention. "
            "A preview for opening a viewer on a bare lens; once a layer exists, its own `renderAxes` is the one to follow -- the mapping is the view's, not the selection's"
        )
    )
    def suggested_render_axes(self, info: Info) -> "RenderAxes":
        """The default axis mapping a layer over this lens would carry."""
        return coords_logic.resolve_render_axes(self.axis_specs)

    @kante.django_field(description="The lens' axis names, in array order. A selection never drops or reorders an axis")
    def axis_names(self, info: Info) -> List[str]:
        """The lens' axis names."""
        return self.axis_names

    @kante.django_field(
        description=(
            "Every column a label layer over this lens can be coloured or filtered by, with the control each one's role admits -- the same set `createLabelLayer(render: {colorBys: ...})` accepts. "
            "The nested form of the `labelColorByOptions` root query, which is where the search, narrowing and paging live; this one hands back the whole list. It walks the coordinate graph once "
            "per lens, so read it on a lens, not across a page of them"
        )
    )
    def color_by_options(self, info: Info, max_join_depth: int = 1) -> List[Annotated["ColorByOption", strawberry.lazy("core.types.column_options")]]:
        """The picker's candidates, built by the one function the write path validates against."""
        from core.queries.column_options import label_color_by_options as resolve_options

        return resolve_options(info, strawberry.ID(str(self.pk)), max_join_depth=max_join_depth)

    @kante.django_field(description="The shape this lens' slices cut out of its dataset")
    def shape(self, info: Info) -> List[int]:
        """The lens' shape."""
        return self.shape_list

    @kante.django_field(
        description="The edge from this lens' space back into its dataset's intrinsic pixel space. A crop is a translation of the slice starts; a stepped lens also rescales. Without this edge an ROI drawn on a cropped lens has no defined path back to its dataset. Null for an unsliced lens: its space IS the intrinsic space, and there is no shift to record",
    )
    def to_parent(self, info: Info) -> Transformation | None:
        """The stored lens-to-parent edge."""
        return self.to_parent

    @kante.django_field(
        description="The datasets computed from this lens' selection: the direct other end of `derivedFrom`, which names a *lens* as a parent rather than a dataset. An unsliced lens reports what was derived from the whole intrinsic grid -- its space is that grid, so it can say nothing narrower. Like the forward field this reports every child, whether or not this lens is its primary parent and whether or not its geometry survived"
    )
    def derived_datasets(self, info: Info) -> List[ArrayDataset]:
        """The datasets whose derivation edges land in this lens' space."""
        return graph_logic.lens_derived_datasets(self)

    @kante.django_field(description="The slices, in the one normalized spelling: dataset axis order, explicit ints, a step always present. Empty for the whole lens")
    def slices(self, info: Info) -> List[Slice]:
        """The stored slices."""
        return self.slices_list

    @kante.django_field(
        description="Everything needed to reduce one axis of this lens to a phasor: the bin count and width, the period the transform runs over, the laser rate, the instrument-response correction and the persisted distribution. Null when the lens has no MICROTIME or SPECTRUM axis. Derived -- none of it is stored on the lens, and a phasor render node references it rather than copying it, so two layers over one dataset cannot disagree about the instrument"
    )
    def phasor(self, info: Info, axis: str | None = None, harmonic: int = 1) -> "PhasorContext | None":
        """The phasor context of one axis of this lens, at one harmonic."""
        return resolve_phasor_context(self, axis_name=axis, harmonic=harmonic)


_WINDOWS_DESCRIPTION = "The windows, in the one normalized spelling: container axis order, floats, an open side as null. Empty for the whole lens"


@kante.django_type(models.TableLens, filters=filters.LensFilter, ordering=order.LensOrder, pagination=True, description="A selection over a table dataset by value windows on its coordinate columns: the rows kept. Lives in the table's own space")
class TableLens(Lens, OrgScoped):
    """A selection over a table dataset."""

    id: auto
    table_dataset: Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")]

    @kante.django_field(
        description="What this selection structurally is: its table's `spec` vocabulary, read over what its windows leave free. Materialized when the lens is made, and what the `tableSpec` filter reads. A coordinate column a window pins to one value (min == max) no longer counts: one frame of a [POINTS_2D, TIMED, TRACKED] table is [POINTS_2D, TRACKED], and one z-plane of a POINTS_3D table is POINTS_2D. A window that only bounds changes nothing -- the server never scans the rows, so it cannot say they stopped spreading. The whole lens answers what its table does"
    )
    def spec(self, info: Info) -> List[enums.TableDatasetSpec]:
        """Every spec the table's columns satisfy under the lens' pins."""
        return self.spec

    @kante.django_field(description=_WINDOWS_DESCRIPTION)
    def windows(self, info: Info) -> List[Window]:
        """The stored windows."""
        return self.windows_list


@kante.django_type(models.SparseLens, filters=filters.LensFilter, ordering=order.LensOrder, pagination=True, description="A selection over a sparse dataset by windows on its axes: the positions kept, never re-indexed. Lives in the dataset's own space")
class SparseLens(Lens, OrgScoped):
    """A selection over a sparse dataset."""

    id: auto
    sparse_dataset: Annotated["SparseDataset", strawberry.lazy("core.types.sparse_dataset")]

    @kante.django_field(
        description="What this selection structurally is: its matrix's `spec` vocabulary, read over what its windows leave free. Materialized when the lens is made, and what the `sparseSpec` filter reads. An axis a window pins to one position (min == max) no longer counts, so one feature of a MATRIX is a VECTOR -- and a SLICE when it pins exactly the axes the matrix identifies itself by and a layout makes the read contiguous, which is what a SPARSE colour or filter picker entry accepts"
    )
    def spec(self, info: Info) -> List[enums.SparseDatasetSpec]:
        """Every spec the matrix satisfies under the lens' windows."""
        return self.spec

    @kante.django_field(description=_WINDOWS_DESCRIPTION)
    def windows(self, info: Info) -> List[Window]:
        """The stored windows."""
        return self.windows_list


@kante.django_type(models.MeshLens, filters=filters.LensFilter, ordering=order.LensOrder, pagination=True, description="A selection over a mesh collection by windows in its own space")
class MeshLens(Lens, OrgScoped):
    """A selection over a mesh collection."""

    id: auto
    mesh_collection: Annotated["MeshCollection", strawberry.lazy("core.types.coords")]

    @kante.django_field(description=_WINDOWS_DESCRIPTION)
    def windows(self, info: Info) -> List[Window]:
        """The stored windows."""
        return self.windows_list


@kante.django_type(models.NetworkLens, filters=filters.LensFilter, ordering=order.LensOrder, pagination=True, description="A selection over a network collection by windows in its own space")
class NetworkLens(Lens, OrgScoped):
    """A selection over a network collection."""

    id: auto
    network_collection: Annotated["NetworkCollection", strawberry.lazy("core.types.coords")]

    @kante.django_field(description=_WINDOWS_DESCRIPTION)
    def windows(self, info: Info) -> List[Window]:
        """The stored windows."""
        return self.windows_list


@kante.django_type(models.AnnotationLens, filters=filters.LensFilter, ordering=order.LensOrder, pagination=True, description="A selection over an annotation collection by windows in its drawing space")
class AnnotationLens(Lens, OrgScoped):
    """A selection over an annotation collection."""

    id: auto
    annotation_collection: "AnnotationCollection"

    @kante.django_field(description=_WINDOWS_DESCRIPTION)
    def windows(self, info: Info) -> List[Window]:
        """The stored windows."""
        return self.windows_list

    @kante.django_field(
        description=(
            "The collection's annotations this lens keeps: those whose own reach -- vertices and pins, in the drawing space the windows are stated in -- meets every window. Every "
            "annotation for a whole lens. Decided per shape, so it costs a read of the collection"
        )
    )
    def annotations(self, info: Info) -> List["Annotation"]:
        """The shapes inside the windows: every one of the collection's for the whole lens."""
        del info
        return descriptors_logic.annotations_in_windows(self.annotation_collection, [] if self.is_whole else self.windows_list)


@kante.django_type(
    models.AnimationWaypoint,
    pagination=True,
    description="One camera pose in a tour, and how the viewer travels to it",
)
class AnimationWaypoint:
    """One camera pose in a tour, and how the viewer travels to it."""

    id: auto
    animation: "Animation" = kante.django_field(description="The tour this pose belongs to")
    order: int = kante.django_field(description="The pose's index in the tour. Written by enumeration when the tour is authored, so it always runs 0, 1, 2 ... with no gaps")
    name: str = kante.django_field(description="What this stop shows, e.g. 'the nucleus'")
    duration_ms: int = kante.django_field(description="How long the viewer takes to travel TO this pose, in milliseconds. Ignored for the first pose, which is where the tour starts")
    easing: enums.Easing = kante.django_field(description="How the viewer eases the camera along that travel")

    @kante.django_field(description="Where the camera is: a position keyed by the world's axis names, plus the flat and volumetric views of it")
    def camera(self, info: Info) -> CameraState:
        """The camera pose, rehydrated from its stored dump."""
        return CameraStateModel(**self.camera)


@kante.django_type(
    models.Animation,
    filters=filters.AnimationFilter,
    ordering=order.AnimationOrder,
    pagination=True,
    description="A named camera tour of a scene: the poses a viewer pans through, in order. A view artifact -- it cascades with the scene, no placement walk crosses it, and refining a registration moves the data but never the camera",
)
class Animation:
    """A named camera tour of a scene: the poses a viewer pans through, in order."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    id: auto
    scene: "Scene" = kante.django_field(description="The scene this tour flies through")
    name: str
    description: str | None
    waypoints: List[AnimationWaypoint] = kante.django_field(description="The poses the viewer pans through, in tour order")
    created_at: datetime.datetime
    creator: User | None
    created_through: Task | None = kante.django_field(description="The task this tour was created through, if any")
    created_through_by: User | None = kante.django_field(description="The assigner of the creating task, if any")

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the list to the request's organization: a bare list field would return every row in the table, across organizations."""
        return build_prescoped_queryset(info, queryset)


@kante.django_type(
    models.SceneSnapshot,
    filters=filters.SceneSnapshotFilter,
    ordering=order.SceneSnapshotOrder,
    pagination=True,
    description="A pre-rendered picture of a composition: every layer of the scene, blended. Clients use snapshots to preview without compositing the layers themselves. A picture of the scene, not of any one lens in it -- `Lens.latestSnapshot` and `ArrayDataset.latestSnapshot` offer the newest picture of the scene the lens nominates, which may stage other data too",
)
class SceneSnapshot:
    """A pre-rendered picture of a composition: every layer of the scene, blended."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    id: auto
    scene: "Scene" = kante.django_field(description="The composition this is a picture of")
    store: MediaStore = kante.django_field(description="The media store holding the rendered image. Ask it for a presignedUrl or an accessGrant to actually fetch the bytes")
    name: str
    created_at: datetime.datetime
    creator: User | None
    created_through: Task | None = kante.django_field(description="The task this snapshot was created through, if any")
    created_through_by: User | None = kante.django_field(description="The assigner of the creating task, if any")

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the list to the request's organization: a bare list field would return every row in the table, across organizations, and this is not inherited from anywhere."""
        return build_prescoped_queryset(info, queryset)


@kante.type(description="Which axis of a data source maps to screen x, y, z, time and intensity. Derived from the axis types, never stored")
class RenderAxes:
    """Which axis of a data source maps to screen x, y, z, time and intensity."""

    x: str = strawberry.field(description="The axis mapped to screen x: the last (fastest-varying) spatial axis")
    y: str = strawberry.field(description="The axis mapped to screen y: the second-to-last spatial axis")
    z: str | None = strawberry.field(description="The axis mapped to screen z: the third-to-last spatial axis, if the data is volumetric")
    t: str | None = strawberry.field(description="The time axis, if the data has one")
    intensity: str | None = strawberry.field(description="The channel axis, if the data has one")
    phasor: str | None = strawberry.field(description="The axis a phasor may be taken over -- a MICROTIME (FLIM arrival time) or SPECTRUM (wavelength) axis -- if the data has one")
    vector: str | None = strawberry.field(description="The DISPLACEMENT value axis, if the data has one: its positions are the components of a per-point offset, which is what a vector layer draws")



@kante.type(
    description="Everything needed to reduce one axis of a lens to a phasor, at one harmonic. Derived from the dataset's physical space, its lightpath and its phasor spokes; nothing here is stored on the lens. A phasor render node states *which* axis and harmonic to reduce, and reads the rest from here -- the instrument is an acquisition fact, so two layers over one dataset cannot disagree about it"
)
class PhasorContext:
    """Everything needed to reduce one axis of a lens to a phasor, at one harmonic."""

    axis: str = strawberry.field(description="The axis the phasor is taken over")
    axis_type: enums.AxisType = strawberry.field(description="What that axis samples: MICROTIME (so the phase reads as a fluorescence lifetime) or SPECTRUM (so it reads as a spectral centre of mass)")
    bins: int = strawberry.field(description="The number of bins along that axis on this lens: the N of the transform")
    harmonic: int = strawberry.field(description="The harmonic this context was resolved for. It selects the calibration and the distribution below, which are both harmonic-specific")
    bin_width: kanne_scalars.GenericQuantity | None = strawberry.field(
        description="The physical width of one bin, from the physical space that scales the axis: a duration ('0.098 ns') for a microtime axis, a wavelength step ('5 nm') for a spectrum axis. Null when the dataset has no physical space -- the phasor is then computable only in bin units"
    )
    window: kanne_scalars.GenericQuantity | None = strawberry.field(
        description="The period the transform actually runs over on THIS lens: bin_width x bins. It is the frequency to transform at, and the only one available over a spectrum axis. For an uncropped FLIM axis it should agree with laserFrequency -- but a lens that slices its phasor axis narrows it, and then it deliberately does not: the window follows the data, while the laser does not. Null when there is no physical space"
    )
    laser_frequency: kanne_scalars.Frequency | None = strawberry.field(
        description="The repetition rate of the pulsed source, read from the lightpath graph anchored to this data: the clock a FLIM phasor runs on, and what makes a phase an absolute lifetime. Prefer it over `window` for a lifetime, and note the two diverge on a lens that crops its microtime axis. Null for a spectrum axis, or when no lightpath states a rate"
    )
    calibration: "PhasorCalibration | None" = strawberry.field(
        description="The instrument-response correction at this axis and harmonic. Null means the phasor is uncalibrated -- which still renders, its hue is just not traceable to an absolute lifetime"
    )
    phasor_histogram: "PhasorHistogram | None" = strawberry.field(description="The persisted (g, s) density at this axis and harmonic, so a client can range the overlay's colormap without reading the cube. Null until a task has computed one")


def resolve_phasor_context(lens: "models.ArrayLens", axis_name: str | None, harmonic: int) -> "PhasorContext | None":
    """Assemble the phasor context of one axis of a lens.

    The three acquisition facts a phasor needs live in three different places -- the bin width
    in a physical-space edge, the laser rate in a lightpath spoke, the correction in a calibration
    spoke -- and none of them is a property of the lens. Gathering them is this function's whole
    job; a client gets them in the same query as the render node, without a second round trip
    and without a copy on the node that could drift from the instrument.
    """
    axis_specs = lens.axis_specs

    if axis_name:
        axis = next((spec for spec in axis_specs if spec.name == axis_name), None)
        if axis is None or not coords_logic.is_phasor_axis(axis.type):
            return None
    else:
        axis = phasor_logic.phasor_axis(axis_specs)
        if axis is None:
            return None

    dataset = lens.dataset
    bins = lens.get_size_of_axis(axis.name)
    # A lens that *steps* the axis samples every k-th bin: the spacing the transform sees is
    # k dataset bins, and the period it covers is that spacing times the lens' bin count. The
    # context reports the lens' own grid, so a stepped lens renders right rather than being
    # handed a window k times too short.
    step = next((int(slice_.step or 1) for slice_ in lens.slices_list if slice_.axis == axis.name), 1)
    bin_width = _scaled_quantity(_resolve_bin_width(dataset, axis.name, len(axis_specs)), step)

    return PhasorContext(
        axis=axis.name,
        axis_type=enums.AxisType(axis.type),
        bins=bins,
        harmonic=harmonic,
        bin_width=bin_width,
        # The *lens'* bin count, not the dataset's: a lens that slices the axis narrows the
        # window the transform runs over, and saying otherwise would claim a period the data
        # does not cover.
        window=_scaled_quantity(bin_width, bins),
        laser_frequency=_resolve_laser_frequency(dataset),
        calibration=dataset.phasor_calibrations_at(axis.name, harmonic),
        phasor_histogram=dataset.phasor_histogram_at(axis.name, harmonic),
    )


def _resolve_bin_width(dataset: "models.ArrayDataset", axis_name: str, axis_count: int) -> str | None:
    """The physical width of one bin along an axis, from the first physical space that scales it.

    **Takes the axis by name, and resolves it separately in each system.** It used to take one
    integer -- the axis' position in the *lens'* axis list -- and use it twice: once to index
    the composed matrix's diagonal, and once as `Axis.order` on the *physical* system. Nothing
    checked that the physical system's axis at that position was the same axis. Where the two
    orders differ, `binWidth` came back as a real number in the wrong axis' unit, and `window`
    (`binWidth x bins`) inherited it. `assert_unit_matches_type` cannot catch that: a SPECTRUM
    axis and a SPACE axis both demand `[length]`.

    The matrix index stays positional, and must: the composed matrix is built over the
    *intrinsic* system's axis order, which is what `axis_scale` indexes. What changes is that
    the position is now looked up in the system the matrix belongs to, and the unit in the
    system the unit belongs to.
    """
    intrinsic = dataset.intrinsic_coordinate_system
    if intrinsic is None:
        return None

    intrinsic_names = [axis.name for axis in intrinsic.axes.all()]
    if axis_name not in intrinsic_names:
        return None
    axis_index = intrinsic_names.index(axis_name)

    for system in graph_logic.physical_neighbours(dataset.coordinate_system) if dataset.coordinate_system else []:
        edge = models.Transformation.objects.filter(input=intrinsic, output=system).first()
        if edge is None:
            continue

        scale = phasor_logic.axis_scale(phasor_logic.flatten_edge(edge.kind, edge.params, edge.step_pairs), axis_index, axis_count)
        if scale is None:
            continue

        # By name. A physical space that does not carry this axis at all says nothing about
        # its bin width -- so move on to the next neighbour rather than reading whatever
        # happens to sit at the same position.
        unit = next((axis.unit for axis in system.axes.all() if axis.name == axis_name), None)
        if unit:
            return phasor_logic.quantity(scale, unit)

    return None


def _scaled_quantity(quantity: str | None, factor: int) -> str | None:
    """Multiply a "magnitude unit" string by a factor, keeping the unit."""
    if quantity is None:
        return None
    magnitude, _, unit = quantity.partition(" ")
    return phasor_logic.quantity(float(magnitude) * factor, unit)


def _resolve_laser_frequency(dataset: "models.ArrayDataset") -> int | None:
    """The pulsed source's repetition rate, from any lightpath anchored to this dataset."""
    for light_path in models.LightPath.objects.filter(anchor__dataset=dataset):
        frequency = phasor_logic.laser_frequency(light_path.graph or {})
        if frequency is not None:
            return frequency
    return None


@kante.django_interface(
    models.Layer,
    description=(
        "A layer placed in a scene and alpha-blended over the layers below it. It carries view state only: a spatial fact is a coordinate system or a transformation edge, never a "
        "field here, and every spatial question a layer answers -- `pathToWorld`, `placement`, `placementValidity`, `placementInvariance` -- is derived from the graph on read and "
        "stored nowhere, so refining one edge updates every layer that looks through it. Which columns hold a point layer's coordinates is likewise the table dataset's declaration, "
        "not a per-layer copy. The concrete kind carries its own data source and render settings: IntensityLayer, RgbLayer, PhasorLayer and LabelLayer each state a recipe of fixed "
        "shape as fields, ImageLayer carries a composable render graph for the layers that genuinely composite, and AnnotationLayer, PointLayer, TrackLayer, MeshLayer and NetworkLayer "
        "draw through a lens over a collection or a table -- every layer, of every kind, draws through a lens of its source's kind."
    ),
)
class Layer:
    """A layer placed in a scene, carrying the shared placement and compositing settings. No spatial fields."""

    id: auto
    kind: enums.LayerKind
    name: str | None
    scene: Scene
    blending: enums.Blending
    opacity: float
    visible: bool
    order: int

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Select the relations the placement logic reads in Python.

        The optimizer prefetches what the *selection set* names, and a client asking for
        `pathToWorld` names none of these -- but the resolver walks all of them. Without
        this, every layer costs a handful of round trips for relations the row could have
        carried along.

        The axes come as a `prefetch_related` because they are a reverse relation, which
        `select_related` cannot follow: `asAffine` reads the source system's axis order to
        label its matrix's columns, and asking for it per layer is a query per layer.

        Also scopes to the request's organization -- it once only selected relations, which
        made `layers` and `layer(id)` read every organization's layers.
        """
        queryset = build_prescoped_queryset(info, queryset)
        return queryset.select_related(*scene_graph.LAYER_PLACEMENT_RELATIONS).prefetch_related(*scene_graph.LAYER_SOURCE_AXIS_PREFETCH)

    @kante.django_field(
        description="The path of transformation edges from this layer's source coordinate system to its scene's world system. A layer belongs to exactly one scene, so this is the one 'to world' question with a single right answer -- the path uses the layer's dataset facts plus the world's registrations, which are the same for every scene over that space. Null when the layer is unregistered or has no source system; empty when the source already is the world system. Every step is here in full, with its own validity, invariance and provenance, which is what to ask for when you care *how* the data got placed; `asAffine` is the same path composed, for when you only need the map",
    )
    def path_to_world(self, info: Info, at: List[CoordinateInput] | None = None) -> List[PlacementStep] | None:
        """The layer's placement path, as (edge, inverted) steps."""
        steps = scene_graph.for_request(info, self.scene).placement_path(self, at=at_map(at))
        if steps is None:
            return None
        return [PlacementStep(transformation=edge, inverted=inverted) for edge, inverted in steps]

    @kante.django_field(
        description=(
            "This layer's whole `pathToWorld` composed into one affine map -- the same path, same edges, same order, with the flagged steps inverted. Derived on read and stored "
            "nowhere, exactly as the path itself is, so refining one registration moves it. **Null when `pathToWorld` is null** and for the same two reasons; `placement` is what "
            "tells them apart. It errors rather than returning null when a path exists but does not condense: a FIELD step gives its map as the values of an array and has no "
            "closed form, and a singular step cannot be walked backwards -- the error names the transformation that stopped it. Note that `placementInvariance` being AFFINE or "
            "stronger is necessary but not sufficient for this to succeed. `outputAxes` names only the destination axes the path constrains, so pass `strict: true` to be refused "
            "a partial map instead of handed one"
        ),
    )
    def as_affine(self, info: Info, strict: bool = False, at: List[CoordinateInput] | None = None) -> AffinePlacement | None:
        """The layer's placement path composed into one labelled affine map."""
        condensed = scene_graph.for_request(info, self.scene).condensed_placement(self, at=at_map(at))
        if condensed is None:
            return None

        if strict and not condensed.total:
            world_axes = [axis.name for axis in self.scene.world.axes.all()] if self.scene.world else []
            missing = [axis for axis in world_axes if axis not in condensed.output_axes]
            raise ValueError(
                f"This layer's placement does not constrain every axis of its scene's world: it maps onto {condensed.output_axes} and says nothing about {missing}. "
                "That is an honest partial registration, not a failure -- drop `strict` to read the map over the axes it does name, or author a registration that places the data along the rest."
            )

        return AffinePlacement(
            matrix=condensed.matrix,
            input_axes=condensed.input_axes,
            output_axes=condensed.output_axes,
            total=condensed.total,
        )

    @kante.django_field(
        description=(
            "Whether this layer has a place in its scene's world, and if not, why not. A null `pathToWorld` means three different things -- nobody has registered this data "
            "yet, its geometry did not survive the operation that produced it and it can never be placed, or it is registered per index and you have not said which index -- "
            "and a client should not have to guess which. UNREGISTERED is a gap to close; UNMAPPABLE is a fact to badge; CONDITIONAL is a placement to ask again for with `at`. "
            "Pass the same `at` you would pass `pathToWorld` to be told about that coordinate. Derived, never stored"
        ),
    )
    def placement(self, info: Info, at: List[CoordinateInput] | None = None) -> enums.PlacementState:
        """PLACED, CONDITIONAL, UNREGISTERED or UNMAPPABLE."""
        return enums.PlacementState(scene_graph.for_request(info, self.scene).placement_state(self, at=at_map(at)))

    @kante.django_field(
        description=(
            "How much this layer's placement is actually known: the weakest edge on its path to world. UNKNOWN while the path rests on an edge a client marked as guessed, and "
            "when there is no path at all; MANUAL once someone authored the registration; VALIDATED once it was checked, and by construction when the path is empty -- data in "
            "its own space is placed exactly. A layer placed per index reports one of its scoped routes rather than UNKNOWN; pass `at` for that coordinate's exact answer. "
            "Derived, never stored -- and distinct from a single edge's `validity`: this is the minimum over the whole path"
        ),
    )
    def placement_validity(self, info: Info, at: List[CoordinateInput] | None = None) -> enums.PlacementValidity:
        """The weakest validity on the layer's placement path."""
        return enums.PlacementValidity(scene_graph.for_request(info, self.scene).placement_validity(self, at=at_map(at)))

    @kante.django_field(
        description=(
            "Which geometric properties survive the whole walk from this layer's data to its scene's world: the weakest edge on its path. ISOMETRY means a distance measured in "
            "the data IS that distance in world; SIMILARITY means shapes and angles transfer and every length needs one common factor; AFFINE means only parallelism and area "
            "ratios do, so an angle or a distance read in the data means nothing in world; DIFFEOMORPHIC means nothing metric survives anywhere; NONE means there is no path at "
            "all. This is what says whether a scalar length in scene units (`pointSize`, `lineWidth`, a stroke width, a camera zoom) is well defined for this layer: it is, from "
            "SIMILARITY up. A layer placed per index reports one of its scoped routes rather than NONE; pass `at` for that coordinate's exact answer. Derived, never stored -- "
            "and distinct from a single edge's `invariance`, this being the minimum over the whole path. `placement` says which of the reasons a NONE layer has"
        ),
    )
    def placement_invariance(self, info: Info, at: List[CoordinateInput] | None = None) -> enums.TransformInvariance:
        """The weakest invariance class on the layer's placement path."""
        return enums.TransformInvariance(scene_graph.for_request(info, self.scene).placement_invariance(self, at=at_map(at)))


def _level_placements(layer, info: Info) -> List["LevelPlacement"]:
    """One placement per pyramid level of a lens-sourced layer, each anchored at that level's ARRAY system.

    Module-level and shared rather than a method repeated per type. Every kind that sources
    from a lens answers this identically -- the question is about the dataset's pyramid and
    the scene's registrations, and nothing in it depends on how the layer renders -- so five
    copies would be five places for one walk to drift.
    """
    return [
        LevelPlacement(
            data_array=array,
            path=None if steps is None else [PlacementStep(transformation=edge, inverted=inverted) for edge, inverted in steps],
        )
        for array, steps in scene_graph.for_request(info, layer.scene).level_placements(layer)
    ]


#: The description every lens-sourced layer's `levelPaths` carries. One string because it is
#: one field, answered by one function.
_LEVEL_PATHS_DESCRIPTION = (
    "Per pyramid level, the path from that level's voxel grid to this scene's world system. What a multiscale renderer consumes directly: pick a level by zoom and use its path -- "
    "every level stars into the same intrinsic system, so the registration tail is shared. A level's path is null when the dataset is not registered into the scene"
)


_RENDER_AXES_DESCRIPTION = (
    "Which axis of this layer's lens faces screen x, y, z, time and intensity (and which carries the phasor or the vector components). A convention of the *view*, which is why it "
    "is a field of the layer: a lens is a selection and a coordinate system is a container of axes, and neither chooses how it is looked at. Derived on every read from the axis "
    "types and names by the one server-side rule (`core.logic.coords.resolve_render_axes`), never stored, so two layers over one lens -- and two clients over one layer -- cannot "
    "transpose the axes differently. A lens has no such field: a selection does not choose how it is looked at"
)


def _render_axes(layer) -> "RenderAxes":  # noqa: ANN001 - the Layer row behind any lens-backed type
    """The renderer's axis mapping for a lens-backed layer, derived from its lens' axes."""
    return coords_logic.resolve_render_axes(layer.lens.axis_specs)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description=(
        "The general image layer: array (lens) data rendered through a composable render graph. The kind for a layer that actually **composites** -- several channels blended "
        "together, a hand-authored transfer curve, a solid tint, per-channel opacity. When the recipe's shape is fixed instead, one of IntensityLayer, RgbLayer or PhasorLayer says "
        "so directly and carries its settings as fields, which is why those are not this type with a flag. Its rendering is described entirely by the render graph; its placement, "
        "entirely by the coordinate graph."
    ),
)
class ImageLayer(Layer):
    """A layer that composites array (lens) data through a render graph. All placement lives in the coordinate graph."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.IMAGE.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(description="The composable in-layer render graph, if this layer defines one")
    def render_graph(self, info: Info) -> LayerRenderGraph | None:
        if not self.render_graph:
            return None
        return LayerRenderGraphModel(**self.render_graph)

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description=(
        "A single-channel lens through one colormap -- or one solid tint -- with contrast limits and gamma, optionally projected over z. Which channel is drawn is the lens' selection, not a field here. The fluorescence workhorse, and the layer "
        "a renderer can take straight to a single-channel texture and a LUT without walking a tree. Its settings are fields rather than a render graph because there is nothing "
        "here to composite: the graph form of this was a blend node with a single child, and additively blending one thing is that thing. Anything that *would* need the tree -- "
        "a second channel, an authored transfer curve, per-channel opacity -- is an ImageLayer. A tint is not among them: it names the hue one ramp ends at, which is what "
        "`colormap` does, so it is a field here rather than a reason to author a graph."
    ),
)
class IntensityLayer(Layer):
    """One channel of a lens through a colormap or a tint, with contrast limits, gamma and an optional z-projection."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )
    colormap: enums.ColorMap
    color: list[int] | None
    clim_min: float | None
    clim_max: float | None
    gamma: float | None
    projection_mode: enums.ProjectionMode | None

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.INTENSITY.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description=(
        "Three single-channel lenses, each a crop of `lens`, as the red, green and blue components of one picture -- a photograph, a brightfield slide -- sharing one pair of contrast limits. A renderer can "
        "upload this as a single RGB texture in one pass. Its own kind rather than a three-child blend because the two are **indistinguishable as graphs**, and a three-marker "
        "fluorescence acquisition coloured red/green/blue is by far the commoner reading of that shape -- so as a graph the authored fact was destroyed at write time. Never "
        "inferred, for the same reason. There is no per-channel colormap here and no per-channel visibility: the channel *is* the colour, and the three are components of one "
        "picture rather than three signals to hide and reorder separately. Wanting to do that means wanting three layers, or an ImageLayer."
    ),
)
class RgbLayer(Layer):
    """Three channels of a lens as the red, green and blue components of one picture."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )
    red_lens: ArrayLens = kante.django_field(description="The single-channel lens drawn as red: a crop of `lens` with its channel axis at one position")
    green_lens: ArrayLens = kante.django_field(description="The single-channel lens drawn as green: a crop of `lens` with its channel axis at one position")
    blue_lens: ArrayLens = kante.django_field(description="The single-channel lens drawn as blue: a crop of `lens` with its channel axis at one position")
    clim_min: float | None
    clim_max: float | None
    white_balance: List[float] | None = kante.django_field(
        description="Per-component gains [red, green, blue], multiplied into the components before the shared contrast limits. Null: no correction, the same as [1, 1, 1]",
    )

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.RGB.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description=(
        "One axis of a lens -- MICROTIME or SPECTRUM -- reduced per pixel to a phasor and coloured by it: over microtime the phase reads as a fluorescence lifetime, over a spectrum "
        "as a spectral centre of mass. Its recipe lives in `phasorRender` rather than in fields, as a label layer's lives in `labelRender`, because a phasor's transfer maps the "
        "*reduction's* output -- a (g, s) pair plus a photon count -- and carries a list of cursors. A phasor composited *with* an ordinary intensity channel is still expressible: "
        "that is an ImageLayer whose graph carries a `PhasorNode`."
    ),
)
class PhasorLayer(Layer):
    """A layer that reduces one axis of a lens to a phasor and colours the pixel by it."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.PHASOR.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(description="Which axis is reduced to a phasor, at which harmonic, and how the resulting (g, s) becomes color")
    def phasor_render(self, info: Info) -> PhasorRender | None:
        if not self.phasor_render:
            return None
        return PhasorRenderModel(**self.phasor_render)

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description=(
        "A vector-valued lens -- an optical flow, a deformation field, an orientation map -- drawn as glyphs sampled from the grid. The sixth lens-backed kind, and the value "
        "domain is what earns it one, for LabelLayer's reason: the lens carries a DISPLACEMENT axis whose positions are the components of a per-point offset, so the axis is "
        "read as geometry, and none of the intensity vocabulary (a channel, a gamma, a projection) survives that. Which axis holds the components is derived "
        "(`vectorAxis`), never stored -- a per-layer copy could disagree with the axes themselves. What the layer carries is honestly view state: a glyph style, a sampling "
        "stride, a magnitude scale and a magnitude colormap window, and two layers over one field may disagree about all of it -- a coarse overview beside a dense inset."
    ),
)
class VectorLayer(Layer):
    """A layer drawing a vector-valued lens as glyphs sampled from the grid."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )
    glyph: enums.VectorGlyph
    glyph_stride: int | None
    glyph_scale: float | None
    colormap: enums.ColorMap
    color: list[int] | None
    clim_min: float | None
    clim_max: float | None

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.VECTOR.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(
        description=(
            "The lens axis whose positions are the vector components: the DISPLACEMENT value axis. Derived from the axis types on every read and stored nowhere, so two layers "
            "over one field cannot disagree about it. Component ORDER follows the spatial axes' array order -- position i displaces along the i-th spatial axis, slowest-varying "
            "first, so the last component displaces along x. Stated here because a standalone layer has no FIELD edge to name the order per-edge, and a renderer and a writer "
            "silently disagreeing about it draws every arrow transposed"
        )
    )
    def vector_axis(self, info: Info) -> str:
        """The DISPLACEMENT axis, resolved from the lens' axes."""
        return coords_logic.resolve_render_axes(self.lens.axis_specs).vector

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders array (lens) data whose values are discrete object ids -- a segmentation or an instance map. It shares the image layer's source and the same coordinate-graph placement, and none of its render settings: contrast limits, gamma, colormaps and intensity projections are all meaningless over ids.",
)
class LabelLayer(Layer):
    """A layer that renders a segmentation or instance map. Ids, not intensities."""

    id: auto
    lens: ArrayLens = kante.django_field(
        disable_optimization=True,
        description="The array lens this layer draws through: the selection, and the space the data is placed from. Read through the layer's own prefetch rather than the optimizer's, which cannot follow a polymorphic foreign key to the kind",
    )

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.LABEL.value

    @kante.django_field(description=_RENDER_AXES_DESCRIPTION)
    def render_axes(self, info: Info) -> "RenderAxes":
        """Which lens axis faces screen x, y, z, time and intensity, for this view."""
        return _render_axes(self)

    @kante.django_field(description="How this layer's object ids become color: the hashing, the transparent background id, contour-or-fill, the selection, and any `colorBy`")
    def label_render(self, info: Info) -> LabelRender | None:
        if not self.label_render:
            return None
        return LabelRenderModel(**self.label_render)

    @kante.django_field(description=_LEVEL_PATHS_DESCRIPTION)
    def level_paths(self, info: Info) -> List["LevelPlacement"]:
        """One placement per pyramid level, each anchored at that level's ARRAY system."""
        return _level_placements(self, info)


@kante.type(description="The placement of one pyramid level in a layer's scene: the level and its path to the world system")
class LevelPlacement:
    """The placement of one pyramid level in a layer's scene."""

    data_array: "DataArray" = strawberry.field(description="The pyramid level being placed")
    path: List[PlacementStep] | None = strawberry.field(description="The path from this level's voxel grid to the scene's world system, or null when the dataset is not registered into the scene")


@kante.type(description="A discrete coordinate an annotation is pinned to, e.g. a timepoint or a channel")
class Coordinate:
    """A discrete coordinate an annotation is pinned to, e.g. a timepoint or a channel."""

    name: str = strawberry.field(description="The name of the coordinate, e.g. 't' or 'c'")
    value: int = strawberry.field(description="The value along that coordinate")


@kante.type(description="An axis-aligned bounding box, as a min and a max corner")
class BoundingBox:
    """An axis-aligned bounding box, as a min and a max corner."""

    min: list[float] = strawberry.field(description="The lower corner, in the coordinate order of the coordinate system")
    max: list[float] = strawberry.field(description="The upper corner, in the coordinate order of the coordinate system")


@kante.django_type(
    models.AnnotationCollection,
    filters=filters.AnnotationCollectionFilter,
    ordering=order.AnnotationCollectionOrder,
    pagination=True,
    description="A named set of human-drawn annotations, owning the coordinate system they are drawn in. The CRUD counterpart of a table dataset's machine-produced rows: shapes a person draws and edits, sharing one drawing space and one registration story",
)
class AnnotationCollection:
    """A named set of human-drawn annotations, owning the space they are drawn in."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    @kante.django_field(description="The lenses over this annotation collection, oldest first: its whole lens and every window someone has cut. `windowed: true` keeps the windows, `windowed: false` the whole lens, which is also `fullLens`")
    def lenses(self, info: Info, windowed: Optional[bool] = None) -> List["AnnotationLens"]:
        """The container's lenses, optionally only the windowed or only the whole one."""
        lenses = self.lenses.order_by("pk")
        if windowed is None:
            return list(lenses)
        return list(lenses.exclude(windows=[]) if windowed else lenses.filter(windows=[]))

    @kante.django_field(description="The lens that selects this whole annotation collection: what a viewer opens for it, and the handle to pass wherever a `Lens` is asked for and all of it is meant. Every annotation collection is created with one")
    def full_lens(self, info: Info) -> Optional["AnnotationLens"]:
        """The whole lens over this container."""
        return self.lenses.filter(windows=[]).order_by("pk").first()

    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(
        description="The folder this annotation collection is filed in. Organisational only: distinct from `scene`, which says which drawing surface minted it, and from `coordinateSystem`, which says where its shapes are drawn"
    )

    @kante.django_field(
        description=(
            "The files this annotation collection was converted from -- the CZI a converter read to write these arrays, named per series. **Read this alongside `derivedFrom`, not instead of "
            "it**: `derivedFrom` says which *data* this was computed from and relates two coordinate systems, while this says which *bytes* it was read out of and relates to no "
            "space at all, because a file has none. Both can be non-empty and complete"
        ),
        prefetch_related=["file_links__file"],
    )
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this annotation collection was produced from."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(
        description="The files written out of this annotation collection: an OME-TIFF export, a rendered snapshot registered as a file. The mirror of `sourceFiles`",
        prefetch_related=["file_links__file"],
    )
    def exports(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file written out of this annotation collection."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.RENDITION), filters, info)

    id: auto
    name: auto
    description: str | None
    scene: Optional["Scene"] = kante.django_field(description="The scene this collection was minted for as its default drawing surface, or null for a freestanding or dataset-derived collection. Bookkeeping, not placement: the registration edge is what places it")
    coordinate_system: CoordinateSystem = kante.django_field(description="The coordinate system the annotations' vectors are expressed in. The collection owns it; `derivedFrom` relates it to whatever the shapes are drawn over")
    annotations: List["Annotation"] = kante.django_field(description="The annotations in this collection")
    created_at: datetime.datetime
    creator: User | None
    provenance_entries: List["ProvenanceEntry"] = kante.django_field(description="Provenance entries for this annotation collection")

    @kante.django_field(
        description="Every edge from this collection's space back into data the shapes are drawn over, in declared order -- the first is the primary parent, the one that places it. An identity into a scene's world for a scene-minted collection, an identity into a dataset's system for one drawn over an image. Empty for a freestanding collection"
    )
    def derived_from(self, info: Info) -> List["Transformation"]:
        """The edges relating this collection's space to the ones it is drawn over."""
        system = getattr(self, "coordinate_system", None)
        return graph_logic.collection_derivation_edges(system) if system else []

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the list to the request's organization.

        A bare list field returns every row in the table, across organizations -- the
        hole the old `dataRois` field had.
        """
        return build_prescoped_queryset(info, queryset)


@kante.django_type(
    models.Annotation,
    filters=filters.AnnotationFilter,
    ordering=order.AnnotationOrder,
    pagination=True,
    description="A human-drawn shape in an annotation collection's coordinate system. It belongs to the collection, not to a scene: delete the scene and the annotation survives",
)
class Annotation:
    """A human-drawn shape in its collection's coordinate system, described by its vectors and the discrete coordinates it is pinned to."""

    id: auto
    collection: AnnotationCollection = kante.django_field(description="The collection this annotation belongs to; its vectors are expressed in the collection's own coordinate system")
    name: auto
    description: str | None
    kind: enums.AnnotationKind
    vectors: list[list[float]]
    created_with_transforms: int
    stroke_color: list[int] | None = kante.django_field(description="The stroke (outline) color of the geometry, as RGBA")
    fill_color: list[int] | None = kante.django_field(description="The fill color of the geometry, as RGBA, or null for no fill")
    stroke_width: float = kante.django_field(description="The stroke width of the geometry, in the drawing space's units. One number for every direction, so it is a well-defined length only where that space's axes share a scale")
    filled: bool = kante.django_field(description="Whether the geometry is filled with fill_color")
    # Nullable: the creator FK is SET_NULL, so a shape outlives the user who drew it.
    creator: User | None = kante.django_field(description="The user that drew this annotation, or null when that user is gone or a client created it without one")
    provenance_entries: List["ProvenanceEntry"] = kante.django_field(description="Provenance entries for this annotation")

    @kante.django_field(description="The coordinate system this annotation's vectors are expressed in: its collection's own system")
    def coordinate_system(self, info: Info) -> CoordinateSystem | None:
        """The collection's coordinate system, surfaced for convenience."""
        return self.collection.coordinate_system_or_none

    @kante.django_field(description="The discrete coordinates this annotation is pinned to. A coordinate the annotation does not pin is one it spans")
    def coordinates(self, info: Info) -> list[Coordinate]:
        """The annotation's discrete pins, unpacked from the stored name-keyed dict."""
        return [Coordinate(name=name, value=value) for name, value in (self.coordinates or {}).items()]

    @kante.django_field(
        description=(
            "The annotation's bounding box in the nearest intrinsic space its collection's chain reaches, derived from every corner of its geometry (an affine-transformed box is not a box: min/max alone gives a "
            "strictly too-small answer under rotation or shear). Intrinsic, not world: world is scene-owned, and one collection can sit in two scenes under two registrations. **Not always a dataset's pixel grid**: "
            "a registration, or a derivation that changes rank, is not something a box can be pushed across -- it says nothing about the axes it does not name, so there is no extent to give them -- and the box then "
            "stays in the collection's own drawing space. Boxes compare only within one frame, which is why the spatial filters require a collection or coordinate system alongside"
        )
    )
    def intrinsic_bbox(self, info: Info) -> BoundingBox | None:
        """The annotation's bounding box in the frame its collection's chain reaches."""
        if not self.intrinsic_bbox:
            return None
        return BoundingBox(min=self.intrinsic_bbox["min"], max=self.intrinsic_bbox["max"])

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):
        """Scope the list to the request's organization, carrying the system along.

        Through the required collection FK -- the annotation carries no organization
        column of its own. A bare list field returns every row in the table, across
        organizations: the hole the old `dataRois` field had. The select_related is
        for the `coordinateSystem` resolver, which walks collection -> system per
        row and would otherwise cost two queries per annotation.
        """
        return queryset.filter(collection__organization=info.context.request.organization).select_related("collection__coordinate_system")


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders an annotation collection's drawn shapes (polygons, boxes, ellipses, lines, paths) in a scene. One layer per collection: per-shape styling lives on the annotations themselves.",
)
class AnnotationLayer(Layer):
    """A layer that renders an annotation collection's drawn shapes in a scene."""

    id: auto
    lens: AnnotationLens = kante.django_field(disable_optimization=True, description="The annotation lens this layer draws through")

    @kante.django_field(description=_CLIP_DESCRIPTION)
    def clip(self, info: Info) -> List[Window]:
        """The lens' windows as a clip box."""
        del info
        return self.lens.windows_list

    @kante.django_field(description="The annotation collection whose shapes this layer renders: the container of the lens. Its own coordinate system is the layer's space")
    def annotation_collection(self, info: Info) -> AnnotationCollection:
        """The lens' collection."""
        return self.lens.annotation_collection

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.ANNOTATION.value


def _coordinate_column_named(layer: "models.Layer", axis_name: str) -> str | None:
    """The table dataset column that is this layer's x / y / z / t, or None.

    **Name first, position second -- the same rule `resolve_render_axes` applies to an array's
    axes**, and for the same reason: a spatial axis named for the screen says which one it is,
    and one that is not named for the screen says nothing, leaving only the array convention.

    It used to be name-only, and the docstring defended that against deriving x from array
    *position* -- rightly, since a table's coordinate columns are matched to the world by name.
    But it left the case `create_table_axes` was explicitly relaxed to allow with no answer at
    all: a table declaring `centroid_x` / `centroid_y` matched no literal `"x"`, so `xColumn`
    came back **null** and the point cloud simply did not draw, with nothing said.

    The trade-off is real and worth stating. Under the positional fallback such a table now
    renders, but by array convention -- so `centroid_x, centroid_y` gives x=`centroid_y`,
    transposed. That is worse than nothing only if nothing is preferable to a picture; it is
    the same bargain the array path already makes, and the honest end state is for a coordinate
    column to *declare* which screen axis it is, which is an input change (see item 14).
    """
    coordinates = layer.lens.table_dataset.columns_by_role(enums.ColumnRoleChoices.COORDINATE.value)
    if not coordinates:
        return None

    named = {column.name.lower(): column.name for column in coordinates}
    if axis_name in named:
        return named[axis_name]

    # Only the *spatial* columns take part in the fallback: x/y/z are screen directions, and an
    # INDEX or TIME coordinate is not one of them however it is named.
    spatial = [column for column in coordinates if column.axis_type == enums.AxisTypeChoices.SPACE.value]
    if axis_name == "t" or len(spatial) < 2:
        return None

    # The array convention: the last spatial column is x, the one before it y, before that z.
    by_convention = {"x": -1, "y": -2, "z": -3}
    offset = by_convention.get(axis_name)
    if offset is None or len(spatial) < -offset:
        return None
    return spatial[offset].name


def _role_column(layer: "models.Layer", role: str) -> str | None:
    """The name of the table dataset's column of a given role, or None."""
    columns = layer.lens.table_dataset.columns_by_role(role)
    return columns[0].name if columns else None


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders a point cloud (e.g. SMLM localisations, centroids) from a table dataset.",
)
class PointLayer(Layer):
    """A layer that renders a point cloud from a table dataset, placed and styled in a scene."""

    id: auto
    lens: TableLens = kante.django_field(disable_optimization=True, description="The table lens this layer draws through: the rows its windows keep, in the table's own space")

    @kante.django_field(
        description="The table dataset the points are drawn from: the container of the lens. Its declared coordinate columns provide the coordinates and its own system provides the placement -- the column fields below are derived from its schema, never stored per layer"
    )
    def table_dataset(self, info: Info) -> Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")]:
        """The lens' table."""
        return self.lens.table_dataset
    size_column: str | None
    point_size: float | None
    colormap: enums.ColorMap | None

    # The same two pickers a mesh layer publishes, over the same relation and
    # rehydrated by the same models. A point layer's objects have positions and
    # nothing else, so a colouring is the only thing that makes one point differ
    # from another, and the picker is the whole of it.
    active_color_by: int | None = kante.django_field(
        description="Which entry of `colorBys` is drawn, as an index into it. Null means every point takes the flat colour -- the distinction between a position and a measurement drawn at one"
    )

    @kante.django_field(
        field_name="point_color_bys",
        description="The colourings this layer offers, in the order a picker should show them. Each names a column of a table this layer's ids key into, or one slice of a sparse matrix they index, already checked to be reachable. Empty means there is nothing to pick",
    )
    def color_bys(self, info: Info) -> List[LabelColorBy]:
        """The published picker, rehydrated from its stored dumps."""
        del info
        return [color_by_models.LabelColorByModel(**entry) for entry in (self.point_color_bys or [])]

    active_filter_bys: List[int] = kante.django_field(
        description="Which entries of `filterBys` are applied, as indices into it. They combine with AND: a point is drawn when every active rule keeps it. Empty applies none of them, so everything draws"
    )

    @kante.django_field(
        field_name="point_filter_bys",
        description="The filters this layer offers, in the order a picker should show them. Each keeps or drops points by a column of a table this layer's ids key into. Empty means nothing is offered and every point draws",
    )
    def filter_bys(self, info: Info) -> List[LabelFilterBy]:
        """The published filter picker, rehydrated from its stored dumps."""
        del info
        return [filter_by_models.LabelFilterByModel(**entry) for entry in (self.point_filter_bys or [])]

    @kante.django_field(description=_WINDOW_FILTERS_DESCRIPTION)
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' windows as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(description="The coordinate column whose axis is named 'x', from the dataset's declared schema")
    def x_column(self, info: Info) -> str | None:
        """The x-coordinate column."""
        return _coordinate_column_named(self, "x")

    @kante.django_field(description="The coordinate column whose axis is named 'y'")
    def y_column(self, info: Info) -> str | None:
        """The y-coordinate column."""
        return _coordinate_column_named(self, "y")

    @kante.django_field(description="The coordinate column whose axis is named 'z', if any")
    def z_column(self, info: Info) -> str | None:
        """The z-coordinate column."""
        return _coordinate_column_named(self, "z")

    @kante.django_field(description="The coordinate column whose axis is named 't', if any")
    def t_column(self, info: Info) -> str | None:
        """The time-coordinate column."""
        return _coordinate_column_named(self, "t")

    @kante.django_field(description="The dataset's ID-role column identifying each point, if any")
    def id_column(self, info: Info) -> str | None:
        """The point-id column."""
        return _role_column(self, enums.ColumnRoleChoices.ID.value)

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.POINT.value


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders trajectories (e.g. particle/cell tracks) from a table dataset, grouped by its TRACK_ID column.",
)
class TrackLayer(Layer):
    """A layer that renders trajectories from a table dataset, placed and styled in a scene."""

    id: auto
    lens: TableLens = kante.django_field(disable_optimization=True, description="The table lens this layer draws through: the rows its windows keep, in the table's own space")

    @kante.django_field(description=_WINDOW_FILTERS_DESCRIPTION)
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' windows as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(
        description="The table dataset the tracks are drawn from: the container of the lens. Its coordinate and TRACK_ID columns provide the trajectories -- the column fields below are derived from its schema, never stored per layer"
    )
    def table_dataset(self, info: Info) -> Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")]:
        """The lens' table."""
        return self.lens.table_dataset
    color_by_column: str | None
    line_width: float | None
    colormap: enums.ColorMap | None

    @kante.django_field(description="The dataset's TRACK_ID column, which groups rows into tracks")
    def track_id_column(self, info: Info) -> str | None:
        """The track-id column."""
        return _role_column(self, enums.ColumnRoleChoices.TRACK_ID.value)

    @kante.django_field(description="The coordinate column whose axis is named 'x', from the dataset's declared schema")
    def x_column(self, info: Info) -> str | None:
        """The x-coordinate column."""
        return _coordinate_column_named(self, "x")

    @kante.django_field(description="The coordinate column whose axis is named 'y'")
    def y_column(self, info: Info) -> str | None:
        """The y-coordinate column."""
        return _coordinate_column_named(self, "y")

    @kante.django_field(description="The coordinate column whose axis is named 'z', if any")
    def z_column(self, info: Info) -> str | None:
        """The z-coordinate column."""
        return _coordinate_column_named(self, "z")

    @kante.django_field(description="The coordinate column whose axis is named 't', if any")
    def t_column(self, info: Info) -> str | None:
        """The time-coordinate column."""
        return _coordinate_column_named(self, "t")

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.TRACK.value


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders a 3D mesh (surface reconstruction / isosurface) placed and styled in a scene.",
)
class MeshLayer(Layer):
    """A layer that renders a 3D mesh, placed and styled in a scene."""

    id: auto
    lens: MeshLens = kante.django_field(disable_optimization=True, description="The mesh lens this layer draws through")

    @kante.django_field(description=_CLIP_DESCRIPTION)
    def clip(self, info: Info) -> List[Window]:
        """The lens' windows as a clip box."""
        del info
        return self.lens.windows_list

    @kante.django_field(description="The immutable, coordinate-system-anchored mesh collection this layer renders: the container of the lens. Its geometry is fetched from the collection's Parquet catalog, not through this API")
    def collection(self, info: Info) -> MeshCollection | None:
        """The lens' collection."""
        return self.lens.mesh_collection
    material_color: list[int] | None
    wireframe: bool
    shading: enums.MeshShading
    max_level: int | None
    active_color_by: int | None = kante.django_field(
        description="Which entry of `colorBys` is drawn, as an index into it. Null means the flat `materialColor` is what is drawn -- the distinction between a surface and a measurement rendered on one"
    )

    @kante.django_field(
        field_name="mesh_color_bys",
        description="The colourings this layer offers, in the order a picker should show them. Each is a column of a table this collection's FIELD edge keys into, already checked to be reachable and to exist. Empty means there is nothing to pick and the material color is the rendering",
    )
    def color_bys(self, info: Info) -> List[MeshColorBy]:
        """The published picker, rehydrated from its stored dumps."""
        return [color_by_models.MeshColorByModel(**entry) for entry in (self.mesh_color_bys or [])]

    active_filter_bys: List[int] = kante.django_field(
        description="Which entries of `filterBys` are applied, as indices into it. They combine with AND: an object is drawn when every active rule keeps it. Empty applies none of them, so everything draws"
    )

    @kante.django_field(
        field_name="mesh_filter_bys",
        description="The filters this layer offers, in the order a picker should show them. Each keeps or drops objects by a column of a table this collection's FIELD edge keys into, already checked to be reachable and to exist. Empty means nothing is offered and every object draws",
    )
    def filter_bys(self, info: Info) -> List[MeshFilterBy]:
        """The published filter picker, rehydrated from its stored dumps."""
        return [filter_by_models.MeshFilterByModel(**entry) for entry in (self.mesh_filter_bys or [])]

    @kante.django_field(
        description="The colouring currently drawn: `colorBys[activeColorBy]`, or null when nothing is selected. Derived, never stored -- there is one copy of the choice, and it is the index",
        deprecation_reason="Read `colorBys` and `activeColorBy` instead: a layer now publishes a picker rather than a single colouring, and this field can only ever show one of its entries.",
    )
    def color_by(self, info: Info) -> MeshColorBy | None:
        """The active entry of the picker, or null when the material color is what is drawn."""
        entries = self.mesh_color_bys or []
        index = self.active_color_by
        if index is None or index >= len(entries):
            return None
        return color_by_models.MeshColorByModel(**entries[index])

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.MESH.value


@kante.django_type(
    models.Layer,
    filters=filters.LayerFilter,
    ordering=order.LayerOrder,
    pagination=True,
    description="A layer that renders a node/edge network -- a traced arbor, a vessel tree, a connectome -- placed and styled in a scene. Its segments are drawn as camera-facing quads rather than GL lines, which is what makes a width in scene units meaningful.",
)
class NetworkLayer(Layer):
    """A layer that renders a node/edge network, placed and styled in a scene."""

    id: auto
    lens: NetworkLens = kante.django_field(disable_optimization=True, description="The network lens this layer draws through")

    @kante.django_field(description=_CLIP_DESCRIPTION)
    def clip(self, info: Info) -> List[Window]:
        """The lens' windows as a clip box."""
        del info
        return self.lens.windows_list

    @kante.django_field(description="The immutable, coordinate-system-anchored network collection this layer renders: the container of the lens. Its nodes and edges are fetched from the collection's Parquet catalog, not through this API")
    def collection(self, info: Info) -> NetworkCollection | None:
        """The lens' collection."""
        return self.lens.network_collection
    material_color: list[int] | None
    line_width: float | None = kante.django_field(
        description="The flat width of every segment, in scene units. A well-defined length only from `placementInvariance` SIMILARITY up. Overridden where `nodeSizeColumn` or `edgeWidthColumn` is set"
    )
    node_size_column: str | None = kante.django_field(
        description="The per-node column giving each node's radius, so a segment tapers between its endpoints -- the shape traced morphology actually has. Wins over `edgeWidthColumn`, being the more specific statement"
    )
    edge_width_column: str | None = kante.django_field(
        description="The per-edge column giving each segment one uniform width. Ignored when `nodeSizeColumn` is set"
    )
    directed: bool = kante.django_field(
        description="Whether the edge direction is drawn, e.g. as arrowheads. A render setting, never a fact about the graph: an edge is always stored source-to-target"
    )
    show_nodes: bool = kante.django_field(description="Whether a glyph is drawn at each node as well as the segments between them")
    max_level: int | None
    active_color_by: int | None = kante.django_field(
        description="Which entry of `colorBys` is drawn, as an index into it. Null means the flat `materialColor` is what is drawn"
    )

    @kante.django_field(
        field_name="network_color_bys",
        description="The colourings this layer offers, in the order a picker should show them. A COLUMN or SPARSE entry colours whole objects, already checked reachable; a GRAPH entry colours per node, by a value the collection itself carries, already checked against its manifest. Empty means there is nothing to pick and the material color is the rendering",
    )
    def color_bys(self, info: Info) -> List[NetworkColorBy]:
        """The published picker, rehydrated from its stored dumps."""
        return [color_by_models.NetworkColorByModel(**entry) for entry in (self.network_color_bys or [])]

    active_filter_bys: List[int] = kante.django_field(
        description="Which entries of `filterBys` are applied, as indices into it. They combine with AND: something is drawn when every active rule keeps it. Empty applies none of them, so everything draws"
    )

    @kante.django_field(
        field_name="network_filter_bys",
        description="The filters this layer offers, in the order a picker should show them. A COLUMN or SPARSE rule keeps or drops whole objects; a GRAPH rule hides individual nodes and segments by a per-node value the collection carries. Empty means nothing is offered and everything draws",
    )
    def filter_bys(self, info: Info) -> List[NetworkFilterBy]:
        """The published filter picker, rehydrated from its stored dumps."""
        return [filter_by_models.NetworkFilterByModel(**entry) for entry in (self.network_filter_bys or [])]

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        return isinstance(obj, models.Layer) and obj.kind == enums.LayerKind.NETWORK.value


# The aggregate behind the homepage statistics sidebars. It replaces the Image-shaped
# `imagesStats`, which was the only aggregate the schema exposed while ArrayDataset was taking
# over as the primary container -- see `create_stats_type` for the one-query-per-field
# memoization and the org scoping every aggregate goes through.
ArrayDatasetStats, ArrayDatasetStatsResolver = create_stats_type(
    models.ArrayDataset,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields={"created_at": "created_at"},
    filters=filters.ArrayDatasetFilter,
)
