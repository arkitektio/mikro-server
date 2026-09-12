"""GraphQL types for the coordinate system graph (RFC-5 inspired).

The API ships transformations as **edges** -- ``(input, output, params)`` -- and
leaves the walking to the client. There is deliberately no ``toWorld`` field on
a dataset or a system and no server-side matrix composition: the same dataset
can sit in two scenes under two different registrations, so any single answer
the server gave would be wrong in one of them. The one sanctioned path query
hangs off a *layer* -- ``Layer.pathToWorld`` and ``ImageLayer.levelPaths`` return
ordered lists of :class:`PlacementStep` edges, because a layer belongs to exactly
one scene and so has one destination -- and composing them is still the client's
job. Everything else about a space is asked of the space: its ``registrations``,
its ``placedSystems``, its ``annotations``, its ``inView``.

``Transformation`` is one Django model discriminated by ``kind``, exposed as an
interface whose concrete types unpack ``params`` into typed fields -- the same
shape as ``Layer``. Subtypes reachable only through the interface are *not*
auto-discovered by strawberry and vanish from the SDL without an error, so they
are registered in :data:`transformation_types` and threaded into the schema's
``types=[...]``.
"""

import datetime
from typing import TYPE_CHECKING, Annotated, List, Optional, Union

import strawberry
from django.db.models import Q
from strawberry import auto

import kante
from kante.types import Info

from kanne_server import scalars as kanne_scalars
from datalayer.types import FabriksStore, KonnektionStore, ParquetStore

from core import enums, filters, models, order, scalars
from core.inputs.coords import BoundingBoxInput, CoordinateInput, at_map
from core.logic import file_link as file_link_logic
from core.logic import graph as graph_logic
from core.logic import space_graph
from core.types.auth import ProvenanceEntry, User
from core.types._shared import apply_link_filters


if TYPE_CHECKING:
    # Only for the lazy annotations below (`scenes`, and the owner union's members):
    # importing them at runtime would be a cycle, since both of these modules import
    # this one's CoordinateSystem.
    from core.types.array_dataset import ArrayDataset, Annotation, AnnotationCollection, CoordinateAnchor, DataArray, Lens, Scene
    from core.types.file_link import FileLink
    from core.types.folder import Folder
    from core.types.table_dataset import TableDataset
    from core.types.sparse_dataset import SparseDataset


@kante.django_type(
    models.Axis,
    filters=filters.AxisFilter,
    pagination=True,
    description="One named, typed dimension of a coordinate system. Its `order` is its index into the array shape",
)
class Axis:
    """One named, typed dimension of a coordinate system."""

    id: auto
    order: int
    name: str
    type: enums.AxisType
    # The kanne Unit scalar, not a free-form string: a unit that pint cannot parse
    # is rejected at the API boundary rather than stored and discovered later by
    # whoever tries to convert with it. Null exactly when the axis holds indices
    # rather than measurements: a dataset's or level's pixel grid, a mesh's voxel
    # grid, a table's INDEX axis. Per-axis on purpose -- kind alone cannot say it,
    # since a table's INTRINSIC space is calibrated exactly when its columns were.
    unit: kanne_scalars.Unit | None
    long_name: str | None
    description: str | None


# The container a system hangs off, as one field rather than seven mostly-null ones. Every
# member but MeshCollection lives in a module that imports this one, so each is annotated
# lazily -- the same treatment `CoordinateSystem.scenes` already needs.
#
# The arms mirror `core.logic.graph.CONTAINERS`, which is the list; adding a member there
# without adding it here is how a resident becomes unreturnable.
Resident = Annotated[
    Union[
        Annotated["ArrayDataset", strawberry.lazy("core.types.array_dataset")],
        Annotated["DataArray", strawberry.lazy("core.types.array_dataset")],
        Annotated["Lens", strawberry.lazy("core.types.array_dataset")],
        Annotated["MeshCollection", strawberry.lazy("core.types.coords")],
        Annotated["NetworkCollection", strawberry.lazy("core.types.coords")],
        Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")],
        Annotated["AnnotationCollection", strawberry.lazy("core.types.array_dataset")],
        Annotated["SparseDataset", strawberry.lazy("core.types.sparse_dataset")],
    ],
    strawberry.union("Resident", description="A piece of data living in a coordinate system. Data belongs to a space; the space belongs to nobody"),
]


#: Key for the per-request placeable-set map on the context's loader store, by system pk.
_PLACEABLE_KEY = "placeable_system_ids"


def _placeable_ids(info: Info, system) -> set[int]:
    """The systems placeable in this one, computed once per space per request.

    `placeable_system_ids_in` is not cheap -- a registrations fetch, a residence map (three
    queries), a descendant closure, a lineage-closed edge fetch and a reverse BFS -- and two
    fields answer from it. Selecting both used to walk it twice per system, and over a *list*
    of systems that is 2N walks; the fields it replaced on `Scene` were memoized and carried
    a comment saying exactly this. Keyed by system, because that is what the answer depends
    on: the same space asked twice in one request cannot have two answers.

    Not routed through `space_graph.for_request`, which also fetches every resident of every
    candidate space -- far more than this needs. Falls through uncached off-request, where
    there is no store to cache in.
    """
    loaders = getattr(info.context, "_loaders", None)
    if loaders is None:
        return graph_logic.placeable_system_ids_in(system)
    by_system = loaders.setdefault(_PLACEABLE_KEY, {})
    if system.pk not in by_system:
        by_system[system.pk] = graph_logic.placeable_system_ids_in(system)
    return by_system[system.pk]


@kante.django_type(
    models.CoordinateSystem,
    filters=filters.CoordinateSystemFilter,
    ordering=order.CoordinateSystemOrder,
    pagination=True,
    description="A named coordinate space: a node in the transformation graph. Its axes are ordered, and that order is the order of the array's dimensions",
)
class CoordinateSystem:
    """A named coordinate space: a node in the transformation graph."""

    id: auto
    name: auto
    axes: List[Axis] = kante.django_field(description="The system's axes, in the order the data has them: for a system backed by an array, its store's dimension order; for a table, its coordinate columns as declared. No ordering by type is imposed on either. What matters downstream is that the *spatial* axes are in array order, which is what the render axes are derived from")
    epoch: datetime.datetime | None = kante.django_field(
        description="The wall-clock instant this system's time axis has its origin at: `wall_clock = epoch + t * unit`. A property of the space, not of any composition over it. Meaningful only for a unit-carrying system with a TIME axis (a shared world space); null when the clock is unanchored -- the time axis is still a perfectly composable relative coordinate"
    )
    scenes: List[Annotated["Scene", strawberry.lazy("core.types.array_dataset")]] = kante.django_field(
        filters=filters.SceneFilter,
        ordering=order.SceneOrder,
        pagination=True,
        description="The scenes that compose over this system as their world. A SHARED space lists every scene sharing it and outlives each of them: a scene adopts a space, never owns it, and deleting a scene never deletes the space. The inverse of `Scene.worldCoordinateSystem`",
    )
    provenance_entries: List[ProvenanceEntry] = kante.django_field(description="Provenance entries for this coordinate system: who created it, and every subsequent change")
    created_at: datetime.datetime
    # Nullable: the creator FK is SET_NULL, so a system outlives the user who made it.
    creator: User | None

    # What replaced `kind`. A space used to be labelled by which container pointed back at
    # it -- INTRINSIC, ARRAY, PHYSICAL, SHARED -- but the honest question about a space is
    # what data lives in it, and that is a list rather than an enum. A space nothing lives in
    # is a pure reference frame: a world, an atlas.
    @kante.django_field(
        prefetch_related=list(graph_logic.RESIDENT_RELATIONS),
        description=(
            "The data living in this space. Empty for a pure reference frame -- a world, an atlas -- which is what a space with no residents *is*; there is no separate "
            "kind to consult. Several residents may share one space: a dataset's own pyramid levels and unsliced lenses live in its grid, and a hundred tiles acquired on "
            "one stage can be registered into one stage frame"
        ),
    )
    def residents(self, info: Info) -> List[Resident]:
        """Everything whose `coordinateSystem` is this one, outermost container first."""
        return [resident for relation in graph_logic.RESIDENT_RELATIONS for resident in getattr(self, relation).all()]

    # This used to hang off `Scene`, where its own description conceded it was a property of
    # the space rather than of the scene. Unprefetchable: `Transformation.input`/`output` are
    # `related_name="+"`, so there is no reverse accessor to prefetch and asking a list of
    # systems for their registrations is one query each.
    @kante.django_field(
        description=(
            "Every top-level edge landing in this space -- the claims that place something here. They belong to the space, so every scene composing over this system sees the same "
            "list, each entry unique for its data-tree, and `layers.pathToWorld` searches exactly these plus the datasets' own facts. On a shared world these are the "
            "registrations a client authored; on a container's own grid they are the lens crops and derived children that land in it. Composing the matrices stays the "
            "client's job. Not filtered by kind, so a table's own system also lists the FIELD edge of any mask keying it -- that one places nothing (a FIELD has no "
            "inverse, so no path walks it backwards); it is a dereference, and `attributePlans` is what reads it"
        ),
    )
    def registrations(self, info: Info) -> List["Transformation"]:
        """The top-level edges into this space."""
        return list(models.Transformation.objects.filter(parent__isnull=True, output=self.pk).order_by("pk"))

    # `Scene.coordinateSystems` used to answer this, from a closure the scene built over its
    # world's edges alone -- which under-reported (a placed dataset's physical space, its
    # pyramid grids and its derived children were reachable and never listed) and
    # over-reported (an unregistered layer's own systems were seeded and never removed).
    # There is one correct implementation, it belongs to the space, and this is it.
    @kante.django_field(
        description=(
            "Every space whose data can be composed here: those reaching this one across steps that compose into one affine map, walking the transformation edges. Composed, not merely connected -- a space reaching this one only across a FIELD relates to it by the values of an array and yields no matrix to draw with, so it is not here. The same set the `placeableIn` "
            "filters answer from, so a picker and a layer mutation cannot disagree. Distinct from `coordinateGraph`, which walks the undirected *neighbourhood* -- this is "
            "directed, and asks who can get in"
        ),
    )
    def placed_systems(self, info: Info) -> List["CoordinateSystem"]:
        """The spaces with an affinely composable path into this one; see `graph_logic.placeable_system_ids_in`."""
        # The residents come along: a plain list is opaque to the optimizer, so a client
        # selecting `residents` would otherwise pay six reverse queries per space.
        return list(models.CoordinateSystem.objects.filter(pk__in=_placeable_ids(info, self)).prefetch_related(*graph_logic.RESIDENT_RELATIONS))

    @kante.django_field(
        description=(
            "The annotations drawn in a space that can reach this one. Reachability, not containment: an annotation belongs to a collection, and outlives every scene "
            "composing over this space. A property of the space rather than of any scene over it, which is why it hangs off the system -- ask it of a scene as "
            "`worldCoordinateSystem { annotations }`"
        ),
    )
    def annotations(self, info: Info) -> List[Annotated["Annotation", strawberry.lazy("core.types.array_dataset")]]:
        """The annotations whose collection's system can reach this space."""
        # A collection's own system hangs one edge off whatever it is drawn over, and that
        # edge lands *in* the placeable set rather than being part of it -- so a collection
        # also counts when any of its (non-UNMAPPABLE) edges lands in a placeable space.
        placeable = _placeable_ids(info, self)
        anchored = (
            models.Transformation.objects.filter(parent__isnull=True, input__annotation_collections__isnull=False, output__in=placeable)
            .exclude(kind=enums.TransformKind.UNMAPPABLE.value)
            .values_list("input_id", flat=True)
        )
        return list(
            models.Annotation.objects.filter(Q(collection__coordinate_system__in=placeable) | Q(collection__coordinate_system__in=anchored)).select_related(
                "collection__coordinate_system"
            )
        )

    @kante.django_field(
        prefetch_related=["axes"],
        description=(
            "Which registered sources are in view of an axis-aligned region asked in *this* system's coordinates, each with its extent here, the path of edges that places "
            "it, and its in-view coordinate anchors. The field hangs off the coordinate system because the system IS the frame the region is written in -- there is no "
            "ambient world to be wrong about, and no camera: a region is a box, and projecting a frustum into one is the client's job. `region` names a leading prefix of "
            "this system's axes and says nothing about the rest, so a 2D box asked of a 4D space constrains only its first two axes. Sources the server cannot bound (a "
            "mesh collection's vertices and a table's rows live in Parquet it never opens) come back with an empty `extent` and an `extentState` saying why, rather than "
            "being culled -- refusing to bound something is not the same as knowing it is out of view. Nothing is stored: the extent is composed per request from the "
            "shapes and the edges, so refining a registration moves everything that looks through it and no cached box can disagree. A source registered per index -- a "
            "per-channel or per-timepoint correction -- comes back with `extentState: CONDITIONAL` and no extent unless you pass `at`: it is genuinely in the space, but "
            "which box it occupies depends on the coordinate. Individual annotations are out of "
            "scope; selecting those needs the region pulled back into their frame, and this server composes forward only"
        ),
    )
    def in_view(self, info: Info, region: BoundingBoxInput, at: List[CoordinateInput] | None = None) -> List["SourcePlacement"]:
        """The sources whose extent meets a region asked in this system; see `core.logic.space_graph`."""
        graph = space_graph.for_request(info, self)
        keyed = space_graph.region_from_bounds(self, region.min, region.max)
        # Anchors are thousands of rows on a long timelapse, so they are resolved only when
        # the selection set actually names them.
        wants_anchors = any(field.name == "anchors" for selection in info.selected_fields for field in selection.selections if hasattr(field, "name"))

        placements = []
        for hit in graph.in_view(keyed, with_anchors=wants_anchors, at=at_map(at)):
            path = [PlacementStep(transformation=edge, inverted=inverted) for edge, inverted in hit.path]
            extent = [AxisExtent(axis=axis, min=bounds[0], max=bounds[1]) for axis, bounds in (hit.extent or {}).items()]
            placements.append(
                SourcePlacement(
                    source=hit.source.container,
                    system=hit.source.system,
                    extent=extent,
                    extent_state=enums.ExtentState(hit.extent_state),
                    invariance=enums.TransformInvariance(graph_logic.weakest_invariance(graph_logic.invariance_of(edge) for edge, _ in hit.path)),
                    validity=enums.PlacementValidity(graph_logic.weakest_validity(edge.validity for edge, _ in hit.path)),
                    path=path,
                    anchors=hit.anchors,
                )
            )
        return placements

    # The read half of the staleness comparison: geometry records what the chain was when it
    # was authored (`Annotation.createdWithTransforms`), and this is what the chain is now.
    # Both readings come from `graph_logic.transform_version`, which counts the edges' history
    # rows -- derived on demand rather than denormalized, because a refinement anywhere on the
    # chain would otherwise have to fan out and rewrite every system below it, and one of those
    # writes would eventually be missed.
    @kante.django_field(
        description=(
            "How many times the transformation chain from this system down to its dataset's intrinsic pixel space has been written, as it stands now. Compare it with an "
            "annotation's `createdWithTransforms` to detect staleness: the two agreeing means the geometry was authored against the chain still in force, and them differing "
            "means an edge on the path has been written since. Only the comparison is meaningful -- the number counts history rows, so it also moves when an edge is merely "
            "renamed, which errs towards recomputing a box that did not need it rather than trusting one that did. 0 for a system that IS an intrinsic space, or one with no "
            "path down to pixels (a unit-carrying or shared space -- its coordinates are meaningful on their own). Provenance only: it never takes part in resolving a coordinate"
        ),
    )
    def transform_version(self, info: Info) -> int:
        """The current chain version, the counterpart to `Annotation.createdWithTransforms`."""
        return graph_logic.transform_version(self)


@strawberry.type(description="Where along one axis a transformation applies")
class Selector:
    """One axis name and one discrete index on it."""

    axis: str = strawberry.field(description="The axis of the input system this edge is scoped to")
    index: int = strawberry.field(description="The position along that axis at which this map holds")



@kante.django_interface(
    models.Transformation,
    description="A directed edge of the coordinate graph, mapping `input` to `output`. Direction is always forward. The concrete kind (Scale, Translation, Affine, Sequence, ...) carries the parameters",
)
class Transformation:
    """A directed edge of the coordinate graph, mapping `input` to `output`."""

    id: auto
    kind: enums.TransformKind
    name: str | None
    input: CoordinateSystem | None
    output: CoordinateSystem | None

    # A field, not a column. Every write to this edge already leaves a history row through
    # `provenance`, so counting them answers "has this been rewritten since you last looked"
    # without a counter that a writer can forget to bump -- and only `updateTransformation`
    # ever remembered to. The cache-key contract clients are given
    # (`docs/attribute-plans-api.md`: refetch a plan when any `(id, version)` on it changes)
    # is unchanged: a fresh edge reads 1, and every subsequent write moves it.
    #
    # Prefetched rather than counted per row: a plan query asks for this on its edge and on
    # every step of its path, which is a query each without the hint.
    @kante.django_field(
        prefetch_related=["provenance_entries"],
        description=(
            "How many times this edge has been written, counting the row that created it -- so a new edge reads 1. Only comparison is meaningful: this and the edge's `id` "
            "together are the cache key for anything derived from the edge, and a change means refetch. It counts the same provenance rows `provenanceEntries` lists, so the "
            "audit trail and the token cannot disagree; a rename moves it too, which errs towards refetching something that did not change rather than trusting something that did"
        ),
    )
    def version(self, info: Info) -> int:
        """How many times this edge has been written, read off its provenance."""
        # `getattr`, because the annotation above declares the *GraphQL* field's type while
        # `self` here is the Django row, where the same name is the history manager. Reading
        # it through the manager is what lets the prefetch hint above serve this.
        return len(getattr(self, "provenance_entries").all())

    validity: enums.PlacementValidity = kante.django_field(
        description="How much this map is actually known: VALIDATED for a map the server derived (or one someone checked), INFERRED for numbers read from metadata, MANUAL for an authored registration, UNKNOWN for one its author marked as a guess. A layer's validity is the weakest edge on its path to world"
    )
    @kante.field(
        description=(
            "Where along one axis this edge applies, or null for an edge that holds everywhere -- which is almost every edge. "
            "A per-channel correction is scoped to {axis: \"c\", index: 2}; several such edges over one axis are one piecewise map. "
            "A path query crosses a scoped edge only when it fixes that coordinate with `at`"
        )
    )
    def selector(self, info: Info) -> Selector | None:
        """The edge's per-index scope, unpacked from the stored JSON."""
        stored = self.selector  # type: ignore[attr-defined]
        return Selector(axis=stored["axis"], index=stored["index"]) if stored else None

    value_relation: enums.ValueRelation | None = kante.django_field(
        description="(derivation edges) What the operation this edge records did to the *values*, orthogonal to `kind`: IDENTICAL (a crop -- statistics transfer), TRANSFORMED (a deconvolution -- same quantity, new numbers), CATEGORIZED (a threshold -- values became labels, and a bootstrapped scene renders the data as a label map). Null when unstated, and never present on a registration -- values do not cross a claim between spaces"
    )
    # On the interface, so every concrete kind inherits it: an edge is refined in place
    # (`updateTransformation`), which makes this the *only* place the previous states of a
    # placement exist. It is also what `CoordinateSystem.transformVersion` counts, so the
    # audit trail and the staleness token are one record rather than two that can disagree.
    provenance_entries: List[ProvenanceEntry] = kante.django_field(description="Provenance entries for this edge: who authored it, and every refinement since. A refinement rewrites the edge in place, so this audit trail is where the placement's earlier states live -- and counting these rows along a chain is what `CoordinateSystem.transformVersion` reports")
    created_at: datetime.datetime
    creator: User | None

    # Optimizer *hints*, not a get_queryset override: the axis lists are derived from the
    # endpoints' axes, so those have to ride along with the edge. Passing them as hints
    # lets the optimizer merge them into the queryset it is already building; replacing
    # the queryset instead would throw away the caller's prefetch (a SEQUENCE's children
    # arrive prefetched, and re-querying them per edge is the N+1 this whole field is
    # meant to spare the client).
    @kante.django_field(
        prefetch_related=["input__axes", "output__axes", "parent__input__axes", "parent__output__axes"],
        description="The names of the input axes this edge's parameters are ordered by. `scale`, `translation` and the columns of `affine` follow this order -- which is the input system's axis order, NOT the reading layer's axis names, and the two differ often enough that indexing the arrays against them silently misplaces them. A BY_DIMENSION edge names only the subset of axes it acts on; the axes it does not name are the ones it leaves untouched",
    )
    def input_axes(self, info: Info) -> List[str]:
        """The axis order this edge's parameters are written in, on the input side."""
        return graph_logic.edge_axis_names(self, "input")

    @kante.django_field(
        prefetch_related=["input__axes", "output__axes", "parent__input__axes", "parent__output__axes"],
        description="The names of the output axes this edge produces. For a rank-changing BY_DIMENSION edge (placing a (c,y,x) dataset into a (t,z,y,x) world) this is the subset it maps onto; the world's other axes are untouched",
    )
    def output_axes(self, info: Info) -> List[str]:
        """The axis order this edge's parameters are written in, on the output side."""
        return graph_logic.edge_axis_names(self, "output")

    # Derived, never stored: a column here would be free to contradict `params`, and `params`
    # would be right -- the same principle that makes `CoordinateSystem.kind` read its owner
    # FKs. `kind` and `params` are local columns on the row; the prefetch is for a composite,
    # whose children the classification recurses into.
    @kante.django_field(
        prefetch_related=["children"],
        description=(
            "Which geometric properties survive this edge's map, derived from `kind`: ISOMETRY (distances, angles and areas all transfer), SIMILARITY (angles and length ratios "
            "transfer, absolute lengths scale by one common factor), AFFINE (parallelism and area ratios transfer, angles and distances do not), DIFFEOMORPHIC (topology at best, "
            "and only locally -- the Jacobian varies with position), NONE (nothing corresponds). A SEQUENCE or BY_DIMENSION is the weakest of its children. Stated by "
            "kind, never by inspecting the numbers: an AFFINE edge reads AFFINE even when its matrix happens to be rigid, because separating those needs an SVD. A layer's "
            "`placementInvariance` is the minimum of this over its whole path to world"
        ),
    )
    def invariance(self, info: Info) -> enums.TransformInvariance:
        """The invariance class of this edge's map; see `graph_logic.invariance_of`."""
        return enums.TransformInvariance(graph_logic.invariance_of(self))


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="The identity map: input and output coordinates are the same")
class IdentityTransformation(Transformation):
    """The identity map: input and output coordinates are the same."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.IDENTITY.value


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A per-axis multiplication, with one entry per input axis")
class ScaleTransformation(Transformation):
    """A per-axis multiplication, with one entry per input axis."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.SCALE.value

    @kante.django_field(
        description="The per-axis scale factors, in the axis order of the input system, expressed in the units of the output system's axes (dimensionless between pixel systems, e.g. within a pyramid). Absolute, not relative to another level"
    )
    def scale(self, info: Info) -> List[float]:
        """The per-axis scale factors."""
        return self.params.get("scale", [])


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A per-axis offset, with one entry per input axis")
class TranslationTransformation(Transformation):
    """A per-axis offset, with one entry per input axis."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.TRANSLATION.value

    @kante.django_field(description="The per-axis offsets, in the axis order of the input system")
    def translation(self, info: Info) -> List[float]:
        """The per-axis offsets."""
        return self.params.get("translation", [])


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A general affine map, given as an M x (N+1) matrix with rows outermost")
class AffineTransformation(Transformation):
    """A general affine map, given as an M x (N+1) matrix with rows outermost."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.AFFINE.value

    @kante.django_field(description="The affine matrix, M x (N+1), rows outermost. The last column is the translation")
    def affine(self, info: Info) -> List[List[float]]:
        """The affine matrix."""
        return self.params.get("affine", [])


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A rotation, given as an orthonormal matrix")
class RotationTransformation(Transformation):
    """A rotation, given as an orthonormal matrix."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.ROTATION.value

    @kante.django_field(description="The rotation matrix")
    def affine(self, info: Info) -> List[List[float]]:
        """The rotation matrix."""
        return self.params.get("affine", [])


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A permutation of axes, mapping each input axis to an output axis by name")
class MapAxisTransformation(Transformation):
    """A permutation of axes, mapping each input axis to an output axis by name."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.MAP_AXIS.value

    @kante.django_field(description="The names of the input axes, positionally matched to `outputAxes`")
    def input_axes(self, info: Info) -> List[str]:
        """The input axis names."""
        return self.input_axes or []

    @kante.django_field(description="The names of the output axes, positionally matched to `inputAxes`")
    def output_axes(self, info: Info) -> List[str]:
        """The output axis names."""
        return self.output_axes or []


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="An ordered composition of child transformations, applied first to last")
class SequenceTransformation(Transformation):
    """An ordered composition of child transformations, applied first to last."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.SEQUENCE.value

    transformations: List[Transformation] = kante.django_field(
        field_name="children",
        description="The child transformations, applied first to last. They omit their own input and output: the sequence supplies them",
    )


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A composition of child transformations, each acting on a named subset of the axes")
class ByDimensionTransformation(Transformation):
    """A composition of child transformations, each acting on a named subset of the axes."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.BY_DIMENSION.value

    transformations: List[Transformation] = kante.django_field(
        field_name="children",
        description="The child transformations. Each carries the `inputAxes` and `outputAxes` it acts on",
    )


@kante.django_type(models.Transformation, filters=filters.TransformationFilter, pagination=True, description="A non-affine map given by the values of an array rather than by a formula. The array is a `field`: a node of this graph, not a payload on this edge, so it keeps its own lineage and its axes say what its numbers mean. It has no closed-form inverse, so a placement path never walks it backwards")
class FieldTransformation(Transformation):
    """A non-affine map given by the values of an array, which is itself a node of the graph."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.FIELD.value

    # A node, not the store this edge used to carry: the array is data before it is a map --
    # a label mask has its own lineage, provenance and placement -- and a payload can hold
    # none of that. Read its store through the system's own container.
    #
    # Resolved, not exposed raw: the column is null for a self-dereference (see the model),
    # and a client reading `field: null` on an edge whose whole purpose is its field would
    # have to know that convention to make sense of it. It answers the question instead.
    @kante.django_field(
        only=["kind", "field", "input"],
        description="The coordinate system of the array whose values are this map. Its value axis says what they mean: COORDINATE for absolute positions, DISPLACEMENT for offsets, none at all for a scalar array whose single value is a position. Equal to `input` when the array's own pixels are the map, as for a label mask keying a table of objects",
    )
    def field(self, info: Info) -> "CoordinateSystem | None":
        """The field, or the input when the input is its own field. See the model property."""
        return self.effective_field


@kante.django_type(
    models.Transformation,
    filters=filters.TransformationFilter,
    pagination=True,
    description="A declared NON-correspondence: the two systems are related -- one was computed from the other -- and no point of either maps to a point of the other. It has no parameters, no rank and no matrix, and no placement search will walk it, in either direction. This is what a per-object measurement table's relation to the image it was measured from looks like",
)
class UnmappableTransformation(Transformation):
    """A declared non-correspondence: related spaces, and no map between them."""

    id: auto

    @classmethod
    def is_type_of(cls, obj, info) -> bool:
        """Discriminate on the model's `kind` column."""
        return obj.kind == enums.TransformKind.UNMAPPABLE.value

    @kante.django_field(description="Why the geometry does not survive, if the author said. Purely descriptive: the kind is what the graph acts on, and an absent reason does not make the edge any less of a statement")
    def reason(self, info: Info) -> str | None:
        """Why nothing corresponds."""
        return self.params.get("reason")


@kante.type(
    description="One step of a placement path: a transformation edge, plus whether it is traversed against its stored direction. Each step carries its own map, its own `validity` and its own `invariance`, which is what this shape is for -- a client that only wants the composed answer should ask the layer for `asAffine` instead of composing these itself"
)
class PlacementStep:
    """One step of a placement path: an edge, and the direction it is walked in."""

    transformation: Transformation = strawberry.field(description="The transformation edge this step walks along")
    inverted: bool = strawberry.field(description="True when the edge is traversed output-to-input, so its map must be inverted before composing. Only ever set on a step that has an inverse -- a rank-changing edge and a warp field are never offered backwards")


@kante.type(
    description=(
        "A whole placement path composed into one affine map, labelled with the axes it is written over. `matrix` is M x (N+1) with rows outermost -- the same layout an "
        "AffineTransformation's `affine` uses -- its columns in `inputAxes` order and its last column the translation. **`outputAxes` names only the destination axes the path "
        "actually constrains**: a (c,y,x) dataset registered on (y,x) into a (t,z,y,x) world gets two rows, not four, because the registration says nothing about t and z and a "
        "zero row there would pin the data at their origin rather than leave it unstated. `total` is whether the map covers every destination axis"
    )
)
class AffinePlacement:
    """One placement path condensed into a single affine map, over named axes."""

    matrix: List[List[float]] = strawberry.field(description="The composed map, M x (N+1), rows outermost. One row per axis in `outputAxes`, one column per axis in `inputAxes`, plus a final translation column")
    input_axes: List[str] = strawberry.field(description="The axes the matrix's columns are in, which is the layer's own source coordinate system's axis order")
    output_axes: List[str] = strawberry.field(description="The destination axes the matrix's rows are in, in the world's own axis order. An axis the path says nothing about has no row at all")
    total: bool = strawberry.field(description="Whether `outputAxes` covers every axis of the destination space. False for a partial registration -- an honest map over the axes it names, and silence about the rest")


@kante.type(description="One axis of a source's extent: the range it occupies along a single named axis of the queried coordinate system")
class AxisExtent:
    """One axis of an extent. An axis a source does not constrain has no entry at all."""

    axis: str = strawberry.field(description="The name of the axis, as it is named on the queried coordinate system")
    min: float = strawberry.field(description="The lower bound along this axis, inclusive")
    max: float = strawberry.field(description="The upper bound along this axis, inclusive")


InViewSource = Annotated[
    Union[
        Annotated["ArrayDataset", strawberry.lazy("core.types.array_dataset")],
        # Lazy even though it is defined in this module: it is defined *below* this union,
        # and the alternative is moving a type to satisfy an import order.
        Annotated["MeshCollection", strawberry.lazy("core.types.coords")],
        Annotated["TableDataset", strawberry.lazy("core.types.table_dataset")],
        Annotated["AnnotationCollection", strawberry.lazy("core.types.array_dataset")],
    ],
    strawberry.union(
        "InViewSource",
        description=(
            "A container registered into a coordinate system: the data that can be in view of a region asked in it. Deliberately narrower than `residents` -- a pyramid "
            "level and a lens are systems *of* a dataset rather than separate things in the space, and including them would return the same data several times"
        ),
    ),
]


@kante.type(description="One source in view of a region: where it sits in the queried coordinate system, how it got there, and which of its coordinate anchors are in view")
class SourcePlacement:
    """One container in view of a region asked in a coordinate system."""

    source: InViewSource = strawberry.field(description="The container in view: an array dataset, a mesh collection, a table dataset or an annotation collection")
    system: CoordinateSystem = strawberry.field(
        description=(
            "The source's own coordinate system that `extent` is anchored at and `path` starts from -- its pixel grid, its lens crop, or its collection's space. Which one it "
            "is follows from where the registration into the queried system actually attaches, which is what keeps the walk running forward"
        )
    )
    extent: List[AxisExtent] = strawberry.field(
        description=(
            "The source's axis-aligned extent in the queried system's coordinates, one entry per axis it constrains -- and only those. Usually a proper subset: a (c,y,x) "
            "dataset registered onto the (y,x) of a (t,z,y,x) world is a slab, extended along t and z, and an entry there would be a number nothing measured. Empty when "
            "`extentState` is not KNOWN"
        )
    )
    extent_state: enums.ExtentState = strawberry.field(
        description="Whether the server can state this source's extent, and if not, why not. An empty `extent` alone would conflate a Parquet the server never reads with a warp field on the path"
    )
    invariance: enums.TransformInvariance = strawberry.field(
        description="Which geometric properties survive the walk from this source's data into the queried system: the weakest edge on `path`. The classes nest, so a composition belongs to the weakest group any of its factors belongs to"
    )
    validity: enums.PlacementValidity = strawberry.field(
        description="How much this placement is actually known: the weakest edge on `path`. VALIDATED for a source that already IS the queried system, a placement exact by construction"
    )
    path: List[PlacementStep] = strawberry.field(
        description="The ordered edges from `system` into the queried system, in stored direction with the inversions flagged. Empty when the source's own system IS the queried system. The server returns the steps; composing them stays the client's job, exactly as for `Layer.pathToWorld`"
    )
    anchors: List[Annotated["CoordinateAnchor", strawberry.lazy("core.types.array_dataset")]] = strawberry.field(
        description=(
            "The source's coordinate anchors whose slab overlaps the region. An anchor pins some axes and is global along every axis it omits, so its slab is one voxel wide "
            "where it pins and the container's full extent where it does not. Only an array dataset has anchors; every other source kind reports none, which is not a gap"
        )
    )


@kante.type(
    description="The connected component of the coordinate graph around one system: every coordinate system it relates to, and every top-level edge between them. Reachability is undirected -- an edge pointing *into* the system you started from (the edge into a physical space, say) relates to it just as much as one pointing out -- but every edge is returned in its true stored direction, so composing a path is still the client's job and still needs the inversions flagged"
)
class CoordinateGraph:
    """The subgraph reachable from one coordinate system, edges included."""

    root: CoordinateSystem = strawberry.field(description="The coordinate system the walk started from")
    systems: List[CoordinateSystem] = strawberry.field(description="Every coordinate system reachable from the root, the root included, ordered by ID")
    transformations: List[Transformation] = strawberry.field(description="Every top-level edge with both endpoints in `systems`, ordered by ID. The children of a SEQUENCE / BY_DIMENSION wrapper are not listed here; they hang off their wrapper")


@kante.type(
    description=(
        "The provenance component around one piece of data: every container it was computed from or that was computed from it, transitively, and the derivation edges between "
        "them. The lineage counterpart of `coordinateGraph`, and the difference is which edges are *walked*: that one crosses every edge touching a space, so a registration "
        "pulls in everything else registered into the same world -- the neighbourhood, not the provenance. This crosses derivation edges only, in both directions, because "
        "asking a source what came out of it and asking a product what went into it are the same graph read from two ends"
    )
)
class LineageGraph:
    """The derivation component around one container: its ancestors, its descendants, and the edges."""

    root: CoordinateSystem = strawberry.field(description="The coordinate system the walk started from. Its container is the node the graph is centred on")
    nodes: List[Resident] = strawberry.field(
        description=(
            "Every container in the component, the root's own included, ordered by kind and then by ID. Containers rather than spaces: a lineage is a story about data, and a "
            "dataset's pixel grid, its pyramid levels and its lenses are one node in it rather than three. Empty when the root space belongs to no container -- a world was "
            "computed from nothing and nothing was computed from it"
        )
    )
    edges: List[Transformation] = strawberry.field(
        description=(
            "Every derivation edge with both containers in `nodes`, ordered by ID. Stored direction throughout -- child to source -- so the arrow points the way provenance "
            "reads. **Kind-blind**: an UNMAPPABLE edge is here, and is the point of the kind existing, since 'this came from that and the geometry did not survive' is exactly "
            "how a measurement table hangs off the mask it was measured from. Filter on `kind` for the chain that actually places things; a registration is never here at all"
        )
    )


@kante.django_type(
    models.MeshCollection,
    filters=filters.MeshCollectionFilter,
    pagination=True,
    description="An immutable, versioned collection of meshes, stored as one fabriks prefix. Ask its `store` for an access grant and query the Parquet directly (e.g. with DuckDB) rather than paginating meshes through GraphQL",
)
class MeshCollection:
    """An immutable, versioned collection of meshes, backed by Parquet stores rather than rows."""

    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(
        description="The folder this mesh collection is filed in. Organisational only: it says where a user keeps this collection, never where the meshes sit in space -- that is `coordinateSystem` and the edges out of it"
    )

    @kante.django_field(
        description=(
            "The files this mesh collection was converted from -- the CZI a converter read to write these arrays, named per series. **Read this alongside `derivedFrom`, not instead of "
            "it**: `derivedFrom` says which *data* this was computed from and relates two coordinate systems, while this says which *bytes* it was read out of and relates to no "
            "space at all, because a file has none. Both can be non-empty and complete"
        ),
        prefetch_related=["file_links__file"],
    )
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this mesh collection was produced from."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(
        description="The files written out of this mesh collection: an OME-TIFF export, a rendered snapshot registered as a file. The mirror of `sourceFiles`",
        prefetch_related=["file_links__file"],
    )
    def exports(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file written out of this mesh collection."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.RENDITION), filters, info)

    id: auto
    version: str
    spec_version: str
    # The collection's OWN system, not the dataset's. It used to borrow the source's,
    # which forced the vertices to be exactly in that pixel grid; `derivedFrom` is where
    # the relation now lives, and it can say something a borrowed system could not.
    coordinate_system: CoordinateSystem = kante.django_field(description="The coordinate system the collection's vertices are expressed in. The collection owns it; `derivedFrom` relates it to the data the meshes were extracted from")
    # A store, not a URL: it carries the datalayer access grant the client needs to read it, and
    # it is organization-scoped. A bare URL would sit outside the datalayer entirely -- nothing
    # would sign it and nothing would own it.
    store: FabriksStore = kante.django_field(
        description=(
            "The **fabriks store** holding this collection: one prefix with `fabriks.json`, both catalogs and every octree level. Ask it for a single access grant and you can read all of it -- "
            "the manifest, the indexes and the geometry. Its `grid` and `encoding` were read from that manifest rather than declared through this API, so they describe what was actually "
            "written. Never null: a collection whose bytes are not addressable is not a collection"
        )
    )

    @kante.django_field(description="The octree grid, as read from the store's manifest. Its `cellSize` is in voxels, one size per vertex component -- the same order the catalog's bbox columns use, which is not necessarily the coordinate system's axis order")
    def grid(self, info: Info) -> scalars.Any:
        """The octree grid."""
        return self.grid

    @kante.django_field(description="The geometry encoding: how positions, normals and indices are quantized and compressed")
    def encoding(self, info: Info) -> scalars.Any:
        """The geometry encoding."""
        return self.encoding

    @kante.django_field(
        description=(
            "Every column this collection's objects can be coloured or filtered by, with the control each one's role admits -- the same set `createMeshLayer(colorBys:)` and `filterBys` accept. The "
            "nested form of the `colorByOptions` root query, which is where the search, narrowing and paging live; this one hands back the whole list. It walks the coordinate graph once per "
            "collection, so read it on a collection, not across a page of them"
        )
    )
    def color_by_options(self, info: Info, max_join_depth: int = 1) -> List[Annotated["ColorByOption", strawberry.lazy("core.types.column_options")]]:
        """The picker's candidates, built by the one function the write path validates against."""
        from core.queries.column_options import color_by_options as resolve_options

        return resolve_options(info, strawberry.ID(str(self.pk)), max_join_depth=max_join_depth)

    @kante.django_field(
        description="Every edge from this collection's space back into data the meshes were extracted from, in declared order -- the first is the primary parent, the one that places it. An identity when the meshes are in that grid as-is, a scale when they came off a downsampled one, UNMAPPABLE where the lineage is recorded but no geometry is claimed. Empty for a mesh derived from no data at all. The same relation a derived dataset's `derivedFrom` records"
    )
    def derived_from(self, info: Info) -> List["Transformation"]:
        """The edges relating this collection's space to the ones it came from."""
        system = getattr(self, "coordinate_system", None)
        return graph_logic.collection_derivation_edges(system) if system else []


@kante.django_type(
    models.NetworkCollection,
    filters=filters.NetworkCollectionFilter,
    pagination=True,
    description="An immutable, versioned collection of networks, stored as one konnektion prefix. Ask its `store` for an access grant and query the Parquet directly (e.g. with DuckDB) rather than paginating nodes through GraphQL",
)
class NetworkCollection:
    """An immutable, versioned collection of node/edge networks, backed by Parquet stores rather than rows."""

    folder: Optional[Annotated["Folder", strawberry.lazy("core.types.folder")]] = kante.django_field(
        description="The folder this network collection is filed in. Organisational only: it says where a user keeps this collection, never where the networks sit in space -- that is `coordinateSystem` and the edges out of it"
    )

    @kante.django_field(
        description=(
            "The files this network collection was converted from -- the CZI a converter read to write these arrays, named per series. **Read this alongside `derivedFrom`, not instead of "
            "it**: `derivedFrom` says which *data* this was computed from and relates two coordinate systems, while this says which *bytes* it was read out of and relates to no "
            "space at all, because a file has none. Both can be non-empty and complete"
        ),
        prefetch_related=["file_links__file"],
    )
    def source_files(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file this network collection was produced from."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.SOURCE), filters, info)

    @kante.django_field(
        description="The files written out of this network collection: an OME-TIFF export, a rendered snapshot registered as a file. The mirror of `sourceFiles`",
        prefetch_related=["file_links__file"],
    )
    def exports(self, info: Info, filters: filters.FileLinkFilter | None = strawberry.UNSET) -> List[Annotated["FileLink", strawberry.lazy("core.types.file_link")]]:
        """The links naming a file written out of this network collection."""
        return apply_link_filters(file_link_logic.links_for(self, enums.FileLinkDirectionChoices.RENDITION), filters, info)

    id: auto
    version: str
    spec_version: str
    # The collection's OWN system, not the dataset's. It used to borrow the source's,
    # which forced the node positions to be exactly in that pixel grid; `derivedFrom` is where
    # the relation now lives, and it can say something a borrowed system could not.
    coordinate_system: CoordinateSystem = kante.django_field(description="The coordinate system the collection's node positions are expressed in. The collection owns it; `derivedFrom` relates it to the data the network was traced in")
    # A store, not a URL: it carries the datalayer access grant the client needs to read it, and
    # it is organization-scoped. A bare URL would sit outside the datalayer entirely -- nothing
    # would sign it and nothing would own it.
    store: KonnektionStore = kante.django_field(
        description=(
            "The **konnektion store** holding this collection: one prefix with `konnektion.json`, both catalogs and every octree level. Ask it for a single access grant and you can read all of it -- "
            "the manifest, the indexes and the geometry. Its `grid` and `encoding` were read from that manifest rather than declared through this API, so they describe what was actually "
            "written. Never null: a collection whose bytes are not addressable is not a collection"
        )
    )

    @kante.django_field(description="The octree grid, as read from the store's manifest. Its `cellSize` is in voxels, one size per position component -- the same order the catalog's bbox columns use, which is not necessarily the coordinate system's axis order")
    def grid(self, info: Info) -> scalars.Any:
        """The octree grid."""
        return self.grid

    @kante.django_field(description="The geometry encoding: how positions, edges, node ids, radii and ghosts are quantized and compressed, and which coarsening operations each level ran")
    def encoding(self, info: Info) -> scalars.Any:
        """The geometry encoding."""
        return self.encoding

    @kante.django_field(
        description=(
            "Everything a layer over this collection can be coloured or filtered by -- the same set `createNetworkLayer(colorBys:)` and `filterBys` accept. The graph attributes the collection "
            "itself carries come first (Strahler order, degree, depth, component, a stored radius, a writer's own column -- per node), then every reachable table column and sparse slice (per "
            "object). The nested form of the `networkColorByOptions` root query, which is where the search, narrowing and paging live; this one hands back the whole list. It walks the coordinate "
            "graph once per collection, so read it on a collection, not across a page of them"
        )
    )
    def color_by_options(self, info: Info, max_join_depth: int = 1) -> List[Annotated["ColorByOption", strawberry.lazy("core.types.column_options")]]:
        """The picker's candidates, built by the one function the write path validates against.

        Through the *network* resolver, emphatically: this used to delegate to the mesh-rooted
        root query with a network pk, which looked the id up in the wrong table -- DoesNotExist
        when no mesh row shared it, and silently another collection's options when one did.
        """
        from core.queries.column_options import network_color_by_options as resolve_options

        return resolve_options(info, strawberry.ID(str(self.pk)), max_join_depth=max_join_depth)

    @kante.django_field(
        description="Every edge from this collection's space back into data the network was traced in, in declared order -- the first is the primary parent, the one that places it. An identity when the network is in that grid as-is, a scale when it was traced on a downsampled one, UNMAPPABLE where the lineage is recorded but no geometry is claimed. Empty for a network derived from no data at all. The same relation a derived dataset's `derivedFrom` records"
    )
    def derived_from(self, info: Info) -> List["Transformation"]:
        """The edges relating this collection's space to the ones it came from."""
        system = getattr(self, "coordinate_system", None)
        return graph_logic.collection_derivation_edges(system) if system else []


# Subtypes reachable only through the Transformation interface are not
# auto-discovered by strawberry: without this list they are silently dropped from
# the SDL, with no error at import and no error at query time -- the field simply
# is not there. Mirrors core/types/layers.py.
transformation_types = [
    IdentityTransformation,
    ScaleTransformation,
    TranslationTransformation,
    AffineTransformation,
    RotationTransformation,
    MapAxisTransformation,
    SequenceTransformation,
    ByDimensionTransformation,
    FieldTransformation,
    UnmappableTransformation,
]
