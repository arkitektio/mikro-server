from kante.types import Info
import strawberry
from strawberry_django.optimizer import DjangoOptimizerExtension
from authentikate.strawberry.extension import AuthentikateExtension
from strawberry import ID as StrawberryID
from core import types, models, filters, order
from core import mutations
from core import queries
from core import subscriptions
import strawberry_django
from koherent.strawberry.extension import KoherentExtension
from lightpath.constants import interface_types
from lightpath.inputs.types import element_union_types
from core.input_unions import unionElementOf
from core.inputs.coords import derived_from_union_types, transform_union_types
from core.render.layer.inputs import color_by_union_types
from core.inputs.file_link import file_link_union_types
from core.inputs.identification import identification_union_types
from core.render.layer.constants import layer_render_node_types
from core.types.layers import layer_types
from core.types.attribute_plans import sample_step_types
from core.types.coords import transformation_types
from datalayer.duck import DuckExtension
from typing import Annotated, Iterable, TypeVar
from authentikate.strawberry import AuthExtension, AuthSubscribeExtension
from strawberry_django.pagination import OffsetPaginationInput
from authentikate import models as ak_models
from koherent import models as koherent_models
import datalayer.mutations as datalayer_mutations
import datalayer.scalars as datalayer_scalars
import kante
from core import scalars as core_scalars
from kanne_server import scalars as kanne_scalars
from strawberry.schema.config import StrawberryConfig
from core.logic import tables as table_logic
from core.scoping import get_for_org

ID = Annotated[StrawberryID, strawberry.argument(description="The unique identifier of an object")]
T = TypeVar("T")


def field(permission_classes=None, **kwargs):
    "A wrapper for field that adds default permission classes and extensions."
    if permission_classes:
        pass
    else:
        permission_classes = []
    return strawberry_django.field(extensions=[AuthExtension()], **kwargs)


def mutation(roles: list[str] | None = None, **kwargs) -> strawberry.mutation:
    """A wrapper for mutation that adds default permission classes and extensions."""

    return strawberry_django.mutation(extensions=[AuthExtension(any_role_of=roles or ["admin", "bot"])], **kwargs)


def subscription(**kwargs) -> strawberry.subscription:
    """A wrapper for subscription that adds default permission classes and extensions."""
    return strawberry.subscription(extensions=[AuthSubscribeExtension()], **kwargs)


def _paginate(items: "Iterable[T]", pagination: OffsetPaginationInput | None) -> "list[T]":
    """Apply offset/limit pagination to virtual row/cell id sequences (limit defaults to unset = all)."""
    sliced = list(items)
    if pagination is None:
        return sliced
    sliced = sliced[pagination.offset :]
    if isinstance(pagination.limit, int) and pagination.limit >= 0:
        sliced = sliced[: pagination.limit]
    return sliced


@strawberry.type
class Query:
    tasks: list[types.Task] = field(description="List the Rekuest tasks under which objects were created or changed")
    folders: list[types.Folder] = field(description="List folders (collections of images, files and tables)")
    myfolders: list[types.Folder] = field(description="List folders created by the current user")

    scenes: list[types.Scene] = field(description="List scenes (compositions of layers over array datasets)")
    scene: types.Scene = field(description="Get a single scene by ID")

    layers: list[types.Layer] = field(filters=filters.LayerFilter, ordering=order.LayerOrder, pagination=True, description="List layers placed in scenes (a heterogeneous list of layer kinds)")
    layer: types.Layer = field(description="Get a single layer by ID")

    lenses: list[types.Lens] = field(description="List lenses (parameterized ways of looking at an array dataset)")
    lens: types.Lens = field(description="Get a single lens by ID")

    array_datasets: list[types.ArrayDataset] = field(description="List array datasets (N-dimensional arrays with named dimensions and anchored metadata)")
    array_dataset: types.ArrayDataset = field(description="Get a single array dataset by ID")

    data_arrays: list[types.DataArray] = field(description="List data arrays (the multiscale zarr arrays backing array datasets)")
    data_array: types.DataArray = field(description="Get a single data array by ID")

    annotations: list[types.Annotation] = field(description="List annotations (human-drawn shapes, each in its collection's coordinate system)")
    annotation: types.Annotation = field(description="Get a single annotation by ID")

    annotation_collections: list[types.AnnotationCollection] = field(description="List annotation collections (named sets of human-drawn shapes, each owning the coordinate system they are drawn in)")
    annotation_collection: types.AnnotationCollection = field(description="Get a single annotation collection by ID")

    nearest_annotations = field(
        resolver=queries.nearest_annotations,
        description="The k annotations of one collection nearest to a point, by cube distance between the point and each annotation's intrinsic bounding box (GiST-accelerated; 0 inside the box). Scoped to one collection because boxes only compare within one frame; the point is in the collection's nearest-intrinsic space, in its coordinate order",
    )

    coordinate_systems: list[types.CoordinateSystem] = field(description="List coordinate systems (the nodes of the RFC-5 coordinate graph)")
    coordinate_system: types.CoordinateSystem = field(description="Get a single coordinate system by ID")

    transformations: list[types.Transformation] = field(
        filters=filters.TransformationFilter,
        ordering=order.TransformationOrder,
        pagination=True,
        description="List transformations (the directed edges of the coordinate graph). Compose them client-side; the server never resolves a path to world, because the same dataset can sit in two scenes under two registrations",
    )
    transformation: types.Transformation = field(description="Get a single transformation by ID")

    coordinate_graph = field(
        resolver=queries.coordinate_graph,
        description="Walk the coordinate graph out from one system: every coordinate system it reaches and every top-level edge between them. Reachability is undirected (an edge pointing into the system relates to it as much as one pointing out), the edges keep their true direction, and nothing is composed -- what the list queries cannot answer is 'which edges relate to *this* one', because relatedness is transitive and a filter is not",
    )

    lineage_graph = field(
        resolver=queries.lineage_graph,
        description="Walk the *derivation* edges out from one container and return its provenance component: everything this data was computed from, everything computed from it, transitively in both directions, and the edges between them. Distinct from `coordinateGraph`, which walks every edge touching a space -- a registration there drags in every other dataset registered into the same world, which is a neighbourhood rather than a lineage. Nodes are containers, not spaces: a dataset's grid, its levels and its lenses are one node in a provenance story. Kind-blind, so an UNMAPPABLE edge is included and is the point -- that is how a measurement table hangs off the mask it was measured from; filter on `kind` for the chain that actually places things. Root it at any container's coordinate system",
    )

    attribute_plans = field(
        resolver=queries.attribute_plans,
        description="Every attribute plan reachable from one system: one per FIELD edge landing on a table, discovered across the fact component -- probe a source image and the plans of the instance mask derived from it come back, each carrying the `path` of steps from the probed system to its root. Registrations are never crossed (no scene, no world). A plan is instructions, never attributes -- map along the path, sample this array, look the value up in this parquet -- and takes no coordinate, so a client fetches it once and executes it per hover against the chunks it is already rendering. Cache it against the FIELD edge plus every path step (ids and versions); `maxDepth` bounds the discovery. Each plan is a chain of `hops`: the landing, then every declared reference reachable from it -- `Column.references` into another table, a matrix axis a table identifies, or the same axis walked into the matrix -- up to `maxJoinDepth` (default 1, at most 4; 0 is the landing alone), each bound from the hop before and executed by the client in order. The server reads no store and composes nothing",
    )

    color_by_options = field(
        resolver=queries.color_by_options,
        description=(
            "Every column a mesh collection's objects can be coloured or filtered by: one entry per (joinPath, table, column), with the control its declared role admits. **The set this returns is "
            "exactly the set `createMeshLayer(colorBys:)` and `filterBys` accept** -- same reachability walk, same measure-vs-categorical rule -- which is what makes it an options query rather "
            "than a suggestion. Distinct from `attributePlans`, which answers a different question (how to execute a lookup per hover) over a different set: it walks the whole fact component and "
            "returns plans rooted at a source mask that mesh ids cannot execute, drops tables the write path accepts, and fails outright on a storeless array. Both pickers read these same options, "
            "because both branch on the same split. `joinPath` follows `references` from table to table -- pass an option's path back verbatim to select it. The columns' *values* are not here: a "
            "picker wanting a class list or a numeric range reads them from the parquet it already has an `accessGrant` for"
        ),
    )

    filter_by_options = field(
        resolver=queries.filter_by_options,
        description=(
            "Every column a mesh collection's objects can be filtered by -- **the same set `colorByOptions` returns**, under the name that reads right where a rule is being authored. One relation, "
            "one walk, two names: a colouring and a rule reach the same column through the same join and branch on the same measure-vs-categorical split, so two different sets would mean one of "
            "them was wrong. What differs is what a control *means*: MEASURE takes a `min`/`max` bound here and a colormap there. Same arguments, same `joinPath` to pass back, same invariant -- "
            "everything returned is something `createMeshLayer(filterBys:)` accepts"
        ),
    )

    network_color_by_options = field(
        resolver=queries.network_color_by_options,
        description=(
            "Everything a network layer over one collection can be coloured or filtered by. **The set this returns is exactly the set `createNetworkLayer(colorBys:)` and `filterBys` accept.** "
            "Two halves in one list: the GRAPH options first -- the per-node values the collection itself carries (Strahler order, degree, depth, component, a stored radius, a writer's own "
            "column), named by `graphAttribute` and always MEASURE -- then the COLUMN and SPARSE options from the shared reachability walk, rooted at depth zero so only tables keyed by this "
            "collection's **object** ids (via `createTableDataset(keyedBy: {kind: NETWORK_COLLECTION})`) are offered; the wider fact walk would offer the source image's mask-keyed tables, joins "
            "an object id cannot execute. Same arguments, same `joinPath` to pass back, same no-values rule as every other options query"
        ),
    )

    network_filter_by_options = field(
        resolver=queries.network_filter_by_options,
        description=(
            "Everything a network layer over one collection can be filtered by -- **the same set `networkColorByOptions` returns**, under the name that reads right where a rule is being "
            "authored. A GRAPH option is the one whose rule is per node: `min`/`max` bounds over Strahler order, degree or a radius hide individual nodes and segments, where a COLUMN or SPARSE "
            "rule keeps or drops whole objects. Everything returned is something `createNetworkLayer(filterBys:)` accepts"
        ),
    )

    label_color_by_options = field(
        resolver=queries.label_color_by_options,
        description=(
            "Every column a mask's objects can be coloured or filtered by: `colorByOptions` rooted on the lens a label layer renders instead of on a mesh collection, and the same answer for the "
            "same reason -- a mask's pixel values dereference into a table by exactly the FIELD edge a collection's ids do, so the walk, the measure-vs-categorical rule and the `joinPath` to pass "
            "back are one. **The set this returns is exactly the set `createLabelLayer(render: {colorBys: ...})` and `filterBys` accept.** The columns' *values* are not here: a picker wanting a "
            "class list or a numeric range reads them from the parquet it already has an `accessGrant` for"
        ),
    )

    label_filter_by_options = field(
        resolver=queries.label_filter_by_options,
        description=(
            "Every column a mask's objects can be filtered by -- **the same set `labelColorByOptions` returns**, under the name that reads right where a rule is being authored. One relation, one "
            "walk, two names, exactly as `filterByOptions` pairs with `colorByOptions` over a collection: what differs is what a control *means*, since MEASURE takes a `min`/`max` bound here and a "
            "colormap there. Everything returned is something `createLabelLayer(render: {filterBys: ...})` accepts"
        ),
    )

    mesh_collections: list[types.MeshCollection] = field(description="List mesh collections (immutable, versioned Parquet-backed mesh sets, each in a coordinate system of its own)")
    mesh_collection: types.MeshCollection = field(description="Get a single mesh collection by ID")
    network_collections: list[types.NetworkCollection] = field(description="List network collections (immutable, versioned Parquet-backed node/edge networks, each in a coordinate system of its own)")
    network_collection: types.NetworkCollection = field(description="Get a single network collection by ID")

    sparse_datasets: list[types.SparseDataset] = field(description="List sparse datasets (matrices over two enumerated axes, stored as anndata-spelled zarr groups)")
    sparse_dataset: types.SparseDataset = field(description="Get a single sparse dataset by ID")

    table_datasets: list[types.TableDataset] = field(description="List table datasets (Parquet-backed tables of scientific records: measurements, localizations, expression levels)")
    table_dataset: types.TableDataset = field(description="Get a single table dataset by ID")

    children = field(resolver=queries.children, description="List the child folders of a folder")

    scene_snapshots: list[types.SceneSnapshot] = field(description="List scene snapshots (pre-rendered pictures of a composition, for previewing it without compositing the layers)")

    animations: list[types.Animation] = field(description="List animations (named camera tours through a scene)")

    files: list[types.File] = field(description="List files (raw microscopy files such as .czi or .ome.tiff)")
    myfiles: list[types.File] = field(description="List files created by the current user")


    permissions = field(
        resolver=queries.permissions,
        description="Get permissions for a specific object",
    )
    available_permissions = field(
        resolver=queries.available_permissions,
        description="Get available permissions for a specific identifier",
    )
    array_datasets_stats: types.ArrayDatasetStats = field(resolver=types.ArrayDatasetStatsResolver, description="Get statistics about array datasets: counts and time-bucketed series over the datasets of the current organization")

    @field(permission_classes=[], description="List the memberships of the current organization (excluding bots)")
    def members(self, info: Info) -> list[types.Membership]:
        """Return all memberships for the current organization, excluding those with the 'bot' role."""
        return ak_models.Membership.objects.filter(organization=info.context.request.organization).exclude(roles__contains="bot").distinct()

    @field(permission_classes=[], description="Get a single Rekuest task by ID")
    def task(self, info: Info, id: ID) -> types.Task:
        return get_for_org(koherent_models.Task, info, id=id)

    @field(permission_classes=[], description="Get a single scene snapshot by ID")
    def scene_snapshot(self, info: Info, id: ID) -> types.SceneSnapshot:
        return get_for_org(models.SceneSnapshot, info, id=id)

    @field(permission_classes=[], description="Get a single animation by ID")
    def animation(self, info: Info, id: ID) -> types.Animation:
        return get_for_org(models.Animation, info, id=id)

    @field(permission_classes=[], description="Get generic key-value descriptors for an object identified by identifier and ID")
    def describe(self, info: Info, identifier: str, id: strawberry.ID) -> list[types.Descriptor]:
        descriptors = []

        if identifier == "@mikro/file":
            file = get_for_org(models.File, info, id=id)

            if file.name:
                descriptors.append(types.Descriptor(key="name", value=file.name))
            if file.store:
                descriptors.append(types.Descriptor(key="bucket", value=file.store.bucket))
        else:
            raise NotImplementedError(f"Describe not implemented for identifier {identifier}")

        return descriptors

    @field(permission_classes=[], description="Get a single file by ID")
    def file(self, info: Info, id: ID) -> types.File:
        return get_for_org(models.File, info, id=id)

    @field(permission_classes=[], description="Get a single folder by ID")
    def folder(self, info: Info, id: ID) -> types.Folder:
        return get_for_org(models.Folder, info, id=id)


@strawberry.type
class Mutation:

    request_media_upload = kante.django_mutation(
        description="Upload media and return a URL for access",
        resolver=datalayer_mutations.request_media_upload,
    )
    finish_media_upload = kante.django_mutation(
        description="Finalize a media upload after the client has written the object",
        resolver=datalayer_mutations.finish_media_upload,
    )
    request_media_access = kante.django_mutation(
        description="Request temporary S3 read credentials for a media file",
        resolver=datalayer_mutations.request_media_access,
    )
    request_general_media_access = kante.django_mutation(
        description="Request temporary S3 read credentials for media files in the organization",
        resolver=datalayer_mutations.request_general_media_access,
    )

    request_bigfile_upload = kante.django_mutation(
        description="Request an upload grant for a big file store",
        resolver=datalayer_mutations.request_bigfile_upload,
    )
    finish_bigfile_upload = kante.django_mutation(
        description="Finalize a big file upload after the client has written the object",
        resolver=datalayer_mutations.finish_bigfile_upload,
    )
    request_bigfile_access = kante.django_mutation(
        description="Request temporary S3 read credentials for a big file",
        resolver=datalayer_mutations.request_bigfile_access,
    )

    request_zarr_upload = kante.django_mutation(
        description="Request an upload grant for a Zarr store",
        resolver=datalayer_mutations.request_zarr_upload,
    )
    finish_zarr_upload = kante.django_mutation(
        description="Finalize a Zarr upload after the client has written the object",
        resolver=datalayer_mutations.finish_zarr_upload,
    )
    refresh_zarr_upload = kante.django_mutation(
        description=(
            "Reissue upload credentials for a Zarr store whose upload is still in flight. A grant's credentials expire, and clients hold the session token as a static credential, so a "
            "write large enough to outlive its session dies partway through. This returns a fresh session against the same prefix so the write can carry on. Refuses a store that is "
            "already populated -- that is an overwrite, not a resumption"
        ),
        resolver=datalayer_mutations.refresh_zarr_upload,
    )
    request_zarr_access = kante.django_mutation(
        description="Request temporary S3 read credentials for a Zarr store",
        resolver=datalayer_mutations.request_zarr_access,
    )
    request_general_zarr_access = kante.django_mutation(
        description="Request temporary S3 read credentials for Zarr files in the organization",
        resolver=datalayer_mutations.request_general_zarr_access,
    )

    request_sparse_upload = kante.django_mutation(
        description=(
            "Request an upload grant for a sparse store. The grant covers the whole prefix, so one request authorizes the group's metadata and all three of its arrays. It declares "
            "nothing about the matrix: the group states its encoding, shape and chunking, and the server reads them when the upload is finished"
        ),
        resolver=datalayer_mutations.request_sparse_upload,
    )
    finish_sparse_upload = kante.django_mutation(
        description=(
            "Finalize a sparse upload, which is when the group's own metadata is read. A missing encoding, a missing array, or an `indptr` whose length contradicts the declared shape "
            "are all refused here -- that is what an interrupted upload looks like, and catching it now beats a reader discovering it later"
        ),
        resolver=datalayer_mutations.finish_sparse_upload,
    )
    refresh_sparse_upload = kante.django_mutation(
        description=(
            "Reissue upload credentials for a sparse store whose upload is still in flight, for the reason `refreshZarrUpload` exists: three chunked arrays of a large matrix take "
            "long enough that a write can outlive its session token. Refuses a store that is already populated -- that is an overwrite, not a resumption"
        ),
        resolver=datalayer_mutations.refresh_sparse_upload,
    )
    request_sparse_access = kante.django_mutation(
        description="Request temporary S3 read credentials for a sparse store. Covers the whole prefix, because a lookup needs `indptr` before it knows which range of `data` to fetch",
        resolver=datalayer_mutations.request_sparse_access,
    )
    request_general_sparse_access = kante.django_mutation(
        description="Request temporary S3 read credentials for sparse stores in the organization",
        resolver=datalayer_mutations.request_general_sparse_access,
    )

    request_fabriks_upload = kante.django_mutation(
        description="Request an upload grant for a fabriks store. The grant covers the whole prefix, so one request authorizes the manifest, both catalogs and every level",
        resolver=datalayer_mutations.request_fabriks_upload,
    )
    finish_fabriks_upload = kante.django_mutation(
        description="Finalize a fabriks upload. This reads the store's `fabriks.json` and refuses a prefix that has none -- which is what an interrupted upload looks like, since the manifest is written last",
        resolver=datalayer_mutations.finish_fabriks_upload,
    )
    request_fabriks_access = kante.django_mutation(
        description="Request temporary S3 read credentials covering a fabriks store's whole prefix",
        resolver=datalayer_mutations.request_fabriks_access,
    )
    request_general_fabriks_access = kante.django_mutation(
        description="Request temporary S3 read credentials for fabriks stores in the organization",
        resolver=datalayer_mutations.request_general_fabriks_access,
    )
    request_konnektion_upload = kante.django_mutation(
        description="Request an upload grant for a konnektion store. The grant covers the whole prefix, so one request authorizes the manifest, both catalogs and every level",
        resolver=datalayer_mutations.request_konnektion_upload,
    )
    finish_konnektion_upload = kante.django_mutation(
        description="Finalize a konnektion upload. This reads the store's `konnektion.json` and refuses a prefix that has none -- which is what an interrupted upload looks like, since the manifest is written last",
        resolver=datalayer_mutations.finish_konnektion_upload,
    )
    request_konnektion_access = kante.django_mutation(
        description="Request temporary S3 read credentials covering a konnektion store's whole prefix",
        resolver=datalayer_mutations.request_konnektion_access,
    )
    request_general_konnektion_access = kante.django_mutation(
        description="Request temporary S3 read credentials for konnektion stores in the organization",
        resolver=datalayer_mutations.request_general_konnektion_access,
    )

    request_parquet_upload = kante.django_mutation(
        description="Request an upload grant for a Parquet store",
        resolver=datalayer_mutations.request_parquet_upload,
    )
    finish_parquet_upload = kante.django_mutation(
        description="Finalize a Parquet upload after the client has written the object",
        resolver=datalayer_mutations.finish_parquet_upload,
    )
    request_parquet_access = kante.django_mutation(
        description="Request temporary S3 read credentials for a Parquet file",
        resolver=datalayer_mutations.request_parquet_access,
    )
    request_general_parquet_access = kante.django_mutation(
        description="Request temporary S3 read credentials for Parquet files in the organization",
        resolver=datalayer_mutations.request_general_parquet_access,
    )

    # Create A Dataset
    create_array_dataset = mutation(
        resolver=mutations.create_array_dataset,
        description="Create a new dataset from array-like data with optional coordinate anchors and OME metadata",
    )
    update_array_dataset = mutation(
        resolver=mutations.update_array_dataset,
        description="Rename a dataset or redescribe it -- the whole of what is editable, and audited on `provenanceEntries`. Its arrays, axes and coordinate systems are fixed at creation; a recomputation is a new dataset",
    )
    set_default_scene = mutation(
        resolver=mutations.set_default_scene,
        description="Nominate the scene to open for a dataset, and take its thumbnail from. Null clears it",
    )
    delete_array_dataset = mutation(resolver=mutations.delete_array_dataset, description="Delete an existing array dataset")
    create_phasor_histogram = mutation(
        resolver=mutations.create_phasor_histogram,
        description="Attach a phasor distribution (the 2D g/s density at one axis and harmonic) to a dataset, so a client can range a phasor overlay without reading the cube",
    )
    create_phasor_calibration = mutation(
        resolver=mutations.create_phasor_calibration,
        description="Attach an instrument-response correction to a dataset, taking a raw phasor to a calibrated one",
    )
    delete_data_array = mutation(resolver=mutations.delete_data_array, description="Delete an existing data array")

    # A physical space is not a kind of thing (RFC-9): it is an ordinary
    # coordinate system with a transformation edge into it, so `createCoordinateSystem` plus
    # `createTransformation` -- or the `physicalSpace` sugar on `createArrayDataset` -- is the
    # whole story, and there is nothing left for a dedicated mutation pair to do.

    # A coordinate system: a space, built to be related to other spaces by edges and
    # adopted by scenes as their world.
    create_coordinate_system = mutation(
        resolver=mutations.create_coordinate_system,
        description="Create a SHARED coordinate system (an ownerless space) and, in one call, author the edges registering any number of sources (datasets, table datasets, mesh collections, coordinate systems) into it",
    )
    # Shared spaces only, both of them: every other system is named and removed by the
    # container it cascades with, and a shared space answers to nobody.
    update_coordinate_system = mutation(
        resolver=mutations.update_coordinate_system,
        description="Rename a shared coordinate system or anchor its clock. Shared spaces only -- an owned system's name is its container's business, and where data sits is an edge (updateTransformation), not a property of the space",
    )
    delete_coordinate_system = mutation(
        resolver=mutations.delete_coordinate_system,
        description="Delete an unused shared coordinate system. Refused while any scene is rooted in it or any transformation edge touches it. This is the only door a shared space leaves through -- deleting a scene never deletes one. Other system kinds cascade with their owner and cannot be deleted directly",
    )
    clear_coordinate_system = mutation(
        resolver=mutations.clear_coordinate_system,
        description="Delete every registration INTO a shared space in one call, returning the deleted edge ids. The space, the scenes over it (their layers drop to UNREGISTERED) and the space's own claims into wider spaces all survive. Guarded by the space's creator: clearing a space is the space-owner's act",
    )
    delete_orphaned_coordinate_systems = mutation(
        resolver=mutations.delete_orphaned_coordinate_systems,
        description="Delete every orphaned shared space in the organization -- no scene rooted in it, no edge touching it -- and return the deleted ids. The cleanup sweep for the no-garbage-collection policy: scene deletion never deletes a space, this call takes the leftovers back. Org admins sweep every orphan; anyone else sweeps only their own",
    )

    create_annotation = mutation(
        resolver=mutations.create_annotation,
        description="Draw an annotation into a collection, or onto a scene (exactly one of the two). Drawing on a scene finds its annotation collection or mints it on first use: a coordinate system copying the world's axes, an identity registration into the world, and one annotation layer",
    )
    create_annotations = mutation(
        resolver=mutations.create_annotations,
        description="Draw many annotations in one call, into a collection or onto a scene (exactly one of the two, same semantics as createAnnotation). The transform chain and version resolve once for the whole batch, and the rows insert in bulk",
    )
    update_annotation = mutation(
        resolver=mutations.update_annotation,
        description="Edit an annotation: name, kind, vectors, pins or styling. New vectors re-derive the bounding box against the current transform chain",
    )
    delete_annotation = mutation(resolver=mutations.delete_annotation, description="Delete an existing annotation")

    create_annotation_collection = mutation(
        resolver=mutations.create_annotation_collection,
        description="Create an annotation collection explicitly, in a coordinate system of its own, optionally derived from the system the shapes are drawn over. The common path -- drawing on a scene -- goes through createAnnotation instead, which mints the scene's collection on first use",
    )
    delete_annotation_collection = mutation(resolver=mutations.delete_annotation_collection, description="Delete an annotation collection. Its coordinate system, its annotations and its layers cascade with it")

    # Lens

    create_lens = mutation(
        resolver=mutations.create_lens,
        description="Create a new lens from an existing dataset and slicing constraints",
    )
    delete_lens = mutation(resolver=mutations.delete_lens, description="Delete an existing lens")

    create_scene = mutation(
        resolver=mutations.create_scene,
        description="Create a new scene over a world coordinate system: an adopted existing system, or an ordinary SHARED one created for it (never owned by the scene -- it outlives it)",
    )
    # There is no `createSceneFromDataset`. It used to mint a world whose axes copied the
    # dataset's physical space and author an identity edge into it -- a third space that was a
    # copy of the second, and an edge that existed only to justify it. A dataset already has
    # coordinate systems: its pixel grid, and any physical space it is registered into. Stage
    # one of those with the mutation below.
    create_scene_from_coordinate_system = mutation(
        resolver=mutations.create_scene_from_coordinate_system,
        description="Bootstrap a renderable scene over an existing coordinate system: a shared space (its registered sources become layers, up to the policy's nchildren) or an owned system such as a dataset's intrinsic grid or a physical space (the container's own data becomes the layer). The scene adopts the system as its world; no edges are authored. This is how a dataset is staged -- pass `intrinsicSystem` to render in pixels, or a physical space it is registered into to render at physical scale",
    )
    update_scene = mutation(resolver=mutations.update_scene, description="Set a scene's viewer preferences: how a client should open it")
    clear_scene = mutation(
        resolver=mutations.clear_scene,
        description="Delete every layer of a scene, keeping the scene itself. A pure view-state reset: no coordinate system, registration or dataset is touched, and other scenes over the same space never notice",
    )
    delete_scene = mutation(resolver=mutations.delete_scene, description="Delete an existing scene")

    # The coordinate graph. Registration used to be a 4x4 matrix on the layer, where
    # two layers over one dataset carried two copies of one fact; it is now an edge.
    create_transformation = mutation(
        resolver=mutations.create_transformation,
        description="Create one edge of the coordinate graph, mapping an input coordinate system to an output one. This is where registration lives",
    )
    update_transformation = mutation(
        resolver=mutations.update_transformation,
        description="Refine a transformation's parameters, bumping its version",
    )
    delete_transformation = mutation(resolver=mutations.delete_transformation, description="Delete an existing transformation")
    delete_registration = mutation(
        resolver=mutations.delete_registration,
        description="Un-register a source from a space by naming the source and the space rather than the edge. Deletes every edge from the source\'s space into that one -- rivals are allowed, so there is no single edge to mean -- and returns their ids. An UNMAPPABLE declaration is not a placement and is never matched",
    )

    create_mesh_collection = mutation(
        resolver=mutations.create_mesh_collection,
        description="Register an immutable, versioned mesh collection against a coordinate system",
    )
    delete_mesh_collection = mutation(resolver=mutations.delete_mesh_collection, description="Delete an existing mesh collection")
    create_network_collection = mutation(
        resolver=mutations.create_network_collection,
        description="Register an immutable, versioned network collection from an uploaded konnektion store, in a coordinate system of its own",
    )
    delete_network_collection = mutation(resolver=mutations.delete_network_collection, description="Delete an existing network collection")
    create_sparse_dataset = mutation(
        resolver=mutations.create_sparse_dataset,
        description=(
            "Create a sparse dataset from one uploaded sparse store, which holds the matrix in one or more layouts. A sparse matrix is a grid of numbers with no row labels and no "
            "column labels, so **every axis says what its positions are** through its own `identifiedBy` -- a source whose own contents are the ids (which authors a FIELD edge, and "
            "is what makes the matrix reachable from a layer over that source), or the table whose rows they are (which authors a foreign key and no edge). Carried on the axis, "
            "identified-exactly-once is a property of the input rather than a rule this enforces. Nothing about the matrix itself is declared: the spec, the shape, each layout's "
            "encoding and its chunking were read from the store when its upload was finished, and are checked against these axes rather than taken from them"
        ),
    )
    update_sparse_dataset = mutation(resolver=mutations.update_sparse_dataset, description="Rename a sparse dataset or redescribe it -- the whole of what is editable. Its stores, axes and coordinate system are fixed at creation; a recomputation is a new dataset")
    delete_sparse_dataset = mutation(resolver=mutations.delete_sparse_dataset, description="Delete an existing sparse dataset")

    create_table_dataset = mutation(
        resolver=mutations.create_table_dataset,
        description="Create a table dataset from a Parquet store. Its declared coordinate columns become the axes of a coordinate system it owns, which lets a localization table be placed in a scene; a table with no coordinate columns is a measurement table whose rows enumerate objects and whose lineage edge is UNMAPPABLE",
    )
    update_table_dataset = mutation(resolver=mutations.update_table_dataset, description="Rename a table dataset or redescribe it -- the whole of what is editable. Its store, columns and coordinate system are fixed at creation; a recomputation is a new table")
    delete_table_dataset = mutation(resolver=mutations.delete_table_dataset, description="Delete an existing table dataset")

    create_layer = mutation(
        resolver=mutations.create_layer,
        description="Create a general image layer: array (lens) data rendered through a composable render graph. The kind for a layer that actually composites -- several channels together, an authored transfer curve, a tint, per-channel opacity. For a recipe of fixed shape, createIntensityLayer, createRgbLayer, createVolumeLayer and createPhasorLayer make a layer of that kind, whose settings are fields rather than a tree",
    )
    delete_layer = mutation(resolver=mutations.delete_layer, description="Delete an existing layer")
    update_layer = mutation(
        resolver=mutations.update_layer,
        description="Update a general image layer's lens, scene, compositing settings and render graph. Refuses every other kind, naming the mutation that does write its settings -- a layer's kind is fixed for the life of the row",
    )
    create_rgb_layer = mutation(
        resolver=mutations.create_rgb_layer,
        description="Create an RGB layer: three channels of a lens as the red, green and blue components of one picture -- a photograph, a brightfield slide -- sharing one pair of contrast limits. Its own kind rather than a three-channel render graph, because as a graph it is indistinguishable from three fluorescence markers somebody tinted red, green and blue, which is the commoner reading. Never inferred from that shape: a bootstrapped scene reaches this kind only on evidence ingest recorded -- channels labelled red, green and blue, or arrays read out of a PNG -- so state it here for a photograph that arrived with neither",
    )
    update_rgb_layer = mutation(
        resolver=mutations.update_rgb_layer,
        description="Update an RGB layer's channel indices, contrast limits and compositing settings. A patch: what is not sent keeps its current value",
    )
    create_intensity_layer = mutation(
        resolver=mutations.create_intensity_layer,
        description="Create an intensity layer: one channel of a lens through one colormap -- or one solid RGBA tint, for a colour that is a measured fact rather than a choice -- with contrast limits and gamma. The fluorescence workhorse, and its own kind -- its settings are fields, not a render graph, because there is nothing here to composite",
    )
    update_intensity_layer = mutation(
        resolver=mutations.update_intensity_layer,
        description="Update an intensity layer's channel, colormap or tint, contrast limits, gamma, projection and compositing settings. A patch: what is not sent keeps its current value",
    )
    create_label_layer = mutation(
        resolver=mutations.create_label_layer,
        description="Create a label layer that renders an instance / segmentation map -- an array whose values are discrete object ids. Its own layer kind, not an image layer: ids take a hashed colour, a transparent background value and an optional `colorBy` over the table they key into, and none of an image's contrast limits, gamma or colormaps",
    )
    update_label_layer = mutation(
        resolver=mutations.update_label_layer,
        description="Update a label layer's render settings -- the selection, contour, hashing seed or `colorBy`. A patch: what is not sent keeps its current value",
    )
    create_volume_layer = mutation(
        resolver=mutations.create_volume_layer,
        description="Create a single-channel layer rendered as a 3D volume projection (MIP / attenuated-MIP / volume / isosurface). Returns an IntensityLayer with `projectionMode` set, not a kind of its own: a projection collapses z, it does not composite anything, so it is a setting on one channel. Update it with updateIntensityLayer",
    )
    create_phasor_layer = mutation(
        resolver=mutations.create_phasor_layer,
        description="Create a phasor layer, reducing one axis of a lens to a phasor and coloring each pixel by it: a lifetime overlay over a FLIM (microtime) cube, or a spectral one over a hyperspectral cube. For a phasor composited *with* an ordinary intensity channel, use createLayer with a PhasorNode in the graph",
    )
    update_phasor_layer = mutation(
        resolver=mutations.update_phasor_layer,
        description="Update a phasor layer's axis, harmonic, color transfer and compositing settings. A patch: what is not sent keeps its current value, except `transfer`, which replaces the whole transfer when given",
    )
    create_vector_layer = mutation(
        resolver=mutations.create_vector_layer,
        description="Create a vector layer, drawing a vector-valued lens -- an optical flow, a deformation field, an orientation map -- as glyphs sampled from the grid. The lens must carry a DISPLACEMENT value axis of 2 or 3 positions; which axis that is is derived from the axes, never chosen here. What the layer carries is view state: a glyph style, a sampling stride, a magnitude scale and a magnitude colormap window",
    )
    update_vector_layer = mutation(
        resolver=mutations.update_vector_layer,
        description="Update a vector layer's glyph style, sampling stride, magnitude scale, colormap window and compositing settings. A patch: what is not sent keeps its current value",
    )
    create_annotation_layer = mutation(
        resolver=mutations.create_annotation_layer,
        description="Create a layer that renders an annotation collection's drawn shapes in a scene. The explicit path for a second scene: the collection's system must already be registered into that scene's world",
    )
    create_point_layer = mutation(
        resolver=mutations.create_point_layer,
        description="Create a layer that renders a point cloud (e.g. SMLM localisations, centroids) from columns of a table",
    )
    update_point_layer = mutation(
        resolver=mutations.update_point_layer,
        description="Retune a point layer after creation -- above all, switch or republish its colour picker.",
    )
    create_track_layer = mutation(
        resolver=mutations.create_track_layer,
        description="Create a layer that renders trajectories from columns of a table, grouped by a track id",
    )
    update_track_layer = mutation(
        resolver=mutations.update_track_layer,
        description="Retune a track layer after creation -- its line width, its colouring column and the compositing it takes part in.",
    )
    create_mesh_layer = mutation(
        resolver=mutations.create_mesh_layer,
        description="Create a layer that renders a 3D mesh (surface reconstruction / isosurface) in a scene",
    )
    update_mesh_layer = mutation(
        resolver=mutations.update_mesh_layer,
        description="Retune how a mesh layer is drawn: its material, wireframe, compositing, and which table column colours its objects. A patch -- an omitted field keeps its value",
    )
    create_network_layer = mutation(
        resolver=mutations.create_network_layer,
        description="Create a layer that renders a node/edge network -- a traced arbor, a vessel tree, a connectome -- in a scene",
    )
    update_network_layer = mutation(
        resolver=mutations.update_network_layer,
        description="Retune how a network layer is drawn: its colour, its widths, whether direction and nodes are drawn, and the compositing it takes part in. A patch -- an omitted field keeps its value",
    )

    attach_unstructured_meta = mutation(
        resolver=mutations.attach_unstructured_meta,
        description="Attach unstructured metadata to a file",
    )

    link_file = mutation(
        resolver=mutations.link_file,
        description="Record a link between a file and the data it encodes, after both already exist",
    )
    unlink_file = mutation(
        resolver=mutations.unlink_file,
        description="Delete a file link",
    )
    from_file_like = mutation(
        resolver=mutations.from_file_like,
        description="Create a file from file-like data",
    )
    delete_file = mutation(resolver=mutations.delete_file, description="Delete an existing file")

    # Folder
    create_folder = mutation(
        resolver=mutations.create_folder,
        description="Create a new folder to organize data",
    )
    ensure_folder = mutation(
        resolver=mutations.ensure_folder,
        description="Create a new folder to organize data",
    )
    update_folder = mutation(resolver=mutations.update_folder, description="Update folder metadata")
    revert_folder = mutation(
        resolver=mutations.revert_folder,
        description="Revert folder to a previous version",
    )
    pin_folder = mutation(resolver=mutations.pin_folder, description="Pin a folder for quick access")
    delete_folder = mutation(resolver=mutations.delete_folder, description="Delete an existing folder")
    put_folders_in_folder = mutation(
        resolver=mutations.put_folders_in_folder,
        description="Add folders as children of another folder",
    )
    release_folders_from_folder = mutation(
        resolver=mutations.release_folders_from_folder,
        description="Remove folders from being children of another folder",
    )
    put_files_in_folder = mutation(resolver=mutations.put_files_in_folder, description="Add files to a folder")
    release_files_from_folder = mutation(
        resolver=mutations.release_files_from_folder,
        description="Remove files from a folder",
    )
    # Re-filing for the four containers. Without these a container could be filed once, at
    # creation, and never moved -- `folder` was on the create inputs and nowhere else.
    # Releasing unfiles and deletes nothing, the same statement `on_delete=SET_NULL` makes.
    put_array_datasets_in_folder = mutation(resolver=mutations.put_array_datasets_in_folder, description="File array datasets in a folder")
    release_array_datasets_from_folder = mutation(
        resolver=mutations.release_array_datasets_from_folder,
        description="Unfile array datasets from a folder. They are not deleted, only unfiled",
    )
    put_table_datasets_in_folder = mutation(resolver=mutations.put_table_datasets_in_folder, description="File table datasets in a folder")
    release_table_datasets_from_folder = mutation(
        resolver=mutations.release_table_datasets_from_folder,
        description="Unfile table datasets from a folder. They are not deleted, only unfiled",
    )
    put_mesh_collections_in_folder = mutation(resolver=mutations.put_mesh_collections_in_folder, description="File mesh collections in a folder")
    release_mesh_collections_from_folder = mutation(
        resolver=mutations.release_mesh_collections_from_folder,
        description="Unfile mesh collections from a folder. They are not deleted, only unfiled",
    )
    put_annotation_collections_in_folder = mutation(resolver=mutations.put_annotation_collections_in_folder, description="File annotation collections in a folder")
    release_annotation_collections_from_folder = mutation(
        resolver=mutations.release_annotation_collections_from_folder,
        description="Unfile annotation collections from a folder. They are not deleted, only unfiled",
    )

    # SceneSnapshot
    create_scene_snapshot = mutation(resolver=mutations.create_scene_snapshot, description="Adopt an uploaded media file as a pre-rendered picture of a scene")
    delete_scene_snapshot = mutation(resolver=mutations.delete_scene_snapshot, description="Delete an existing scene snapshot")
    pin_scene_snapshot = mutation(resolver=mutations.pin_scene_snapshot, description="Pin a scene snapshot for quick access")

    # Animation
    create_animation = mutation(resolver=mutations.create_animation, description="Author a named camera tour of a scene")
    update_animation = mutation(resolver=mutations.update_animation, description="Re-author a camera tour: rename it, or replace its stops")
    delete_animation = mutation(resolver=mutations.delete_animation, description="Delete an existing camera tour")

    assign_user_permission = mutation(
        resolver=mutations.assign_user_permission,
        description="Assign a user permission to an object",
    )


@strawberry.type
class ChatRoomMessage:
    room_name: str
    current_user: str
    message: str


@strawberry.type
class Subscription:
    files = subscription(resolver=subscriptions.files, description="Subscribe to real-time file updates")


schema = kante.Schema(
    query=Query,
    subscription=Subscription,
    mutation=Mutation,
    extensions=[
        # `only` optimization off, the rest on. It narrows the SELECT to the columns the
        # selection set names, which drops `kind` -- the column every discriminated
        # interface (Layer, Transformation) resolves its concrete type by. `is_type_of`
        # then reads a deferred field, Django refreshes it from the database, and that
        # happens on the event loop thread: "you cannot call this from an async context".
        # Column pruning saves bytes; select_related and prefetch_related save round
        # trips, and those are what the coordinate graph needs.
        DjangoOptimizerExtension(enable_only_optimization=False),
        AuthentikateExtension,
        KoherentExtension,
        DuckExtension,
    ],
    types=[*interface_types, *element_union_types, *layer_render_node_types, *layer_types, *transformation_types, *transform_union_types, *derived_from_union_types, *color_by_union_types, *file_link_union_types, *identification_union_types, *sample_step_types],
    # The union member inputs above are referenced by no field: they are published for
    # codegen, and the directive on each says which flat union input it belongs to.
    schema_directives=[unionElementOf],
    config=StrawberryConfig(scalar_map={**core_scalars.SCALAR_MAP, **datalayer_scalars.SCALAR_MAP, **kanne_scalars.SCALAR_MAP}),
)
