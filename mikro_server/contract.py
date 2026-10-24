"""What this image answers a hub's installer: ``arkitekt-service <verb>`` (see ``arkitekt_service.contract``).

The installer knows the hub; how this release spells its config is written here, with the
settings it is read by. A key renamed in ``configuration.py`` is renamed in :func:`render` in
the same commit, and no installer has to learn of it.
"""

from __future__ import annotations

from arkitekt_service.contract import JSON, Contract, Description, Descriptor, Facts, Hosts, Job, Needs, Offers, Scope, Signal, Start, Structure, blocks

from mikro_server.configuration import Settings
from mikro_server.vocabulary import (
    ANNOTATION_DESCRIPTORS,
    ANNOTATION_SHAPE_DESCRIPTORS,
    ARRAY_DESCRIPTORS,
    FIXTURE_DESCRIPTORS,
    CHART_DESCRIPTORS,
    COORDINATE_ANCHOR_DESCRIPTORS,
    COORDINATE_SYSTEM_DESCRIPTORS,
    FILE_DESCRIPTORS,
    FILE_LINK_DESCRIPTORS,
    MESH_DESCRIPTORS,
    METRIC_DESCRIPTORS,
    NETWORK_DESCRIPTORS,
    SCENE_DESCRIPTORS,
    SPARSE_DESCRIPTORS,
    TABLE_DESCRIPTORS,
    TRANSFORMATION_DESCRIPTORS,
)

#: What a token may be allowed to do here: defined at the coordination server when the hub enrols.
SCOPES = [
    Scope(key="mikro_read", description="Read images from the database"),
    Scope(key="mikro_write", description="Write images to the database"),
    Scope(key="read_image", description="Read image data"),
    Scope(key="read", description="Generic read access"),
    Scope(key="write", description="Generic write access"),
]

#: The roles a member of an organization can hold here.
ROLES = [
    Scope(key="admin", description="Full administrative access"),
    Scope(key="user", description="Standard user access"),
    Scope(key="viewer", description="Read-only access to images"),
    Scope(key="uploader", description="Can upload new images"),
]


def _keys(descriptors: list[Descriptor]) -> list[str]:
    return [descriptor.key for descriptor in descriptors]


#: What exists on a hub because this service is there: said here, as data, so the hub knows it from
#: the image. ``service.py`` binds each of these to its model and refuses anything not said here.
HOSTS = Hosts(
    structures=[
        Structure(
            identifier="@mikro/arraydataset",
            label="Image (Array Dataset)",
            description="A microscopy image or other dense multi-dimensional array: a 2D image, a z-stack or volume, a time-lapse, a multichannel or spectral image, or a label image such as a segmentation mask. Its pixels are stored as a zarr pyramid of resolution levels over one pixel grid.",
            descriptors=ARRAY_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/arraylens",
            label="Array Lens",
            description="A crop or slice of an image (array dataset), cut by index ranges along its axes: one channel, one plane of a z-stack, one timepoint, a rectangular region, or the whole image. It is what an action that processes pixels is handed.",
            descriptors=ARRAY_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/tablelens",
            label="Table Lens",
            description="A subset of a table dataset's rows, those whose coordinate columns (position, time) fall in given value windows, or the whole table. It is what an action that works on rows of measurements, detections or localizations is handed.",
            descriptors=TABLE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/sparselens",
            label="Sparse Lens",
            description="A subset of a sparse dataset, the positions along its axes (objects such as cells, features such as genes) that fall in given windows, or the whole matrix. A single feature picked out to colour objects by is one too.",
            descriptors=SPARSE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/meshlens",
            label="Mesh Lens",
            description="The part of a mesh collection (3D surface meshes) that lies inside given windows of its own space, or the whole collection.",
            descriptors=MESH_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/networklens",
            label="Network Lens",
            description="The nodes and edges of a network collection (traced skeletons, vessel trees, tracking graphs) that lie inside given windows of its own space, or the whole network.",
            descriptors=NETWORK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/annotationlens",
            label="Annotation Lens",
            description="The annotations (hand-drawn regions of interest) of an annotation collection that lie inside given windows of its drawing space, or the whole collection.",
            descriptors=ANNOTATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/fixturelens",
            label="Fixture Lens",
            description="The part of a fixture (a coverslip, a dish, a holder) that lies inside given windows of its own space, or the whole model.",
            descriptors=FIXTURE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/scene",
            label="Scene",
            description="A view that overlays images, label images, points, tracks, annotations, meshes and networks as layers in one shared world coordinate system, displayed in 2D or 3D. It is what a viewer opens to look at registered data together.",
            descriptors=SCENE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/chart",
            label="Chart",
            description="A plot of data laid out along one measured axis (a distance, a time, a wavelength), such as an intensity line profile, a time course or a spectrum. Its layers read image lenses, table series or annotations.",
            descriptors=CHART_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/scenesnapshot",
            label="Scene Snapshot",
            description="A rendered picture of a scene with all its layers blended: a screenshot, preview or thumbnail of the view.",
        ),
        Structure(
            identifier="@mikro/tabledataset",
            label="Table Dataset",
            description="A table of measurements or other scientific records, stored as parquet: one row per segmented object with its features, per detected spot or single-molecule localization (a point cloud), or per cell with its marker levels. Its coordinate columns place the rows in space and time, so points and tracks can be drawn over images.",
            descriptors=TABLE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/meshcollection",
            label="Mesh Collection",
            description="An immutable collection of 3D surface meshes, such as segmented cell or organelle surfaces, stored as an octree of geometry in its own coordinate space.",
            descriptors=MESH_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/networkcollection",
            label="Network Collection",
            description="An immutable collection of node/edge networks with positions: traced neurons and skeletons, vessel trees, connectomes, or tracking graphs with divisions.",
            descriptors=NETWORK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/fixture",
            label="Fixture",
            description="A piece of hardware around the sample, such as a coverslip, a dish, a holder or an objective housing, stored as one binary glTF in a coordinate space of its own whose axes carry the model's units.",
            descriptors=FIXTURE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/file",
            label="File",
            description="An uploaded file, kept exactly as it was uploaded in its original format: a raw microscope file (czi, nd2, lif, tif), a PDF, a spreadsheet or anything else an importer may read.",
            descriptors=FILE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/folder",
            label="Folder",
            description="A folder of uploaded files, like a directory in a file system; folders can be nested.",
        ),
        Structure(
            identifier="@mikro/annotationcollection",
            label="Annotation Collection",
            description="A named set of regions of interest (ROIs) and other annotations drawn by hand, sharing one drawing space that can be registered onto images and scenes.",
            descriptors=ANNOTATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/annotation",
            label="Annotation",
            description="A region of interest (ROI) drawn by hand, one shape of an annotation collection: a point, line, path, polygon, rectangle, circle, ellipse, cube, sphere or ellipsoid, which a person can edit.",
            descriptors=ANNOTATION_SHAPE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/metric",
            label="Metric",
            description="One measurement recorded about an annotation or a dataset, by an app or a person: a quantity with a unit (an area, a count, an intensity), or a code a process emitted. Immutable; a correction supersedes it.",
            descriptors=METRIC_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/animation",
            label="Animation",
            description="A named camera tour of a scene: the viewpoints a viewer flies through, in order.",
        ),
        Structure(
            identifier="@mikro/sparsedataset",
            label="Sparse Dataset",
            description="A sparse matrix of measurements over two enumerations, mostly zeros: objects on one axis and features on the other, such as a cell-by-gene count matrix from spatial transcriptomics.",
            descriptors=SPARSE_DESCRIPTORS,
        ),
        # The coordinate graph, the links and schemas beside the data, and the stores under it:
        # hosted and announced like everything else, with nothing to describe beyond themselves.
        Structure(
            identifier="@mikro/coordinatesystem",
            label="Coordinate System",
            description="A coordinate space that data lives in, such as an image's pixel grid, a physical space in micrometres or a scene's world: a node of the coordinate graph. Transformations map between spaces, which is how pixel size, registration and placement are stated.",
            descriptors=COORDINATE_SYSTEM_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/transformation",
            label="Transformation",
            description="A registration or other map from one coordinate system into another: a scale such as the pixel size, a translation, rotation, affine, or a field read off an array. Image registration and alignment, crops, pyramid levels and derivations are all stated as transformations, the directed edges of the coordinate graph.",
            descriptors=TRANSFORMATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/coordinateanchor",
            label="Coordinate Anchor",
            description="A position in an image, table or sparse dataset that metadata is pinned to: acquisition and microscope settings, OME metadata, channel names, light path and value histograms. An axis it leaves out means the metadata holds all along it.",
            descriptors=COORDINATE_ANCHOR_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/filelink",
            label="File Link",
            description="Links an uploaded file to the dataset (image, table, mesh, ...) holding the same data, and says which was made from which: the dataset imported from the file, or the file exported from the dataset.",
            descriptors=FILE_LINK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/metaschema",
            label="Metadata Schema",
            description="A JSON schema that free-form metadata attached to files is written against.",
        ),
        Structure(
            identifier="@mikro/bigfilestore",
            label="Big File Store",
            description="The stored bytes of an uploaded file in its original format, in object storage.",
        ),
        Structure(
            identifier="@mikro/mediastore",
            label="Media Store",
            description="The stored bytes of a picture or other media object, such as a rendered scene snapshot, in object storage.",
        ),
        Structure(
            identifier="@mikro/zarrstore",
            label="Zarr Store",
            description="The stored zarr array holding an image's pixels: one resolution level of an array dataset, in object storage.",
        ),
        Structure(
            identifier="@mikro/parquetstore",
            label="Parquet Store",
            description="The stored parquet file holding a table dataset's rows, in object storage.",
        ),
        Structure(
            identifier="@mikro/fabriksstore",
            label="Fabriks Store",
            description="The stored octree of surface meshes behind a mesh collection: a fabriks manifest and its parquet files, in object storage.",
        ),
        Structure(
            identifier="@mikro/kulissestore",
            label="Kulisse Store",
            description="The stored device or scene model behind a fixture: one self-contained binary glTF (GLB) with its nodes, joints and bounds, in object storage.",
        ),
        Structure(
            identifier="@mikro/konnektionstore",
            label="Konnektion Store",
            description="The stored octree of node/edge networks behind a network collection: a konnektion manifest and its parquet files, in object storage.",
        ),
        Structure(
            identifier="@mikro/sparsestore",
            label="Sparse Store",
            description="The stored zarr group holding one layout of a sparse matrix behind a sparse dataset, in object storage.",
        ),
    ],
    signals=[
        # A lens of every kind is announced: a crop or a window is an event an action may wait
        # for, and the handle it is then given. `@mikro/is_whole` is how a trigger tells a crop
        # from the whole lens a container is born with.
        Signal(
            identifier="@mikro/arraylens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ARRAY_DESCRIPTORS),
            description="A crop or slice of an image (array lens) was made, renamed or deleted, with its axis counts, channel/timepoint extents, value kind and whether it selects the whole image.",
        ),
        Signal(
            identifier="@mikro/tablelens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(TABLE_DESCRIPTORS),
            description="A subset of a table's rows (table lens) was made, renamed or deleted, with what the table's columns say and whether it selects the whole table.",
        ),
        Signal(
            identifier="@mikro/sparselens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(SPARSE_DESCRIPTORS),
            description="A subset of a sparse matrix (sparse lens) was made, renamed or deleted, with its rank, its layouts and whether it selects the whole matrix.",
        ),
        Signal(
            identifier="@mikro/meshlens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(MESH_DESCRIPTORS),
            description="A part of a mesh collection (mesh lens) was selected, renamed or deleted, with its axes and whether it selects the whole collection.",
        ),
        Signal(
            identifier="@mikro/networklens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(NETWORK_DESCRIPTORS),
            description="A part of a network collection (network lens) was selected, renamed or deleted, with its axes, whether nodes carry attributes and whether it selects the whole network.",
        ),
        Signal(
            identifier="@mikro/annotationlens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ANNOTATION_DESCRIPTORS),
            description="A part of an annotation collection (annotation lens) was selected, renamed or deleted, with how many annotations it holds and whether it selects the whole collection.",
        ),
        Signal(
            identifier="@mikro/fixturelens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(FIXTURE_DESCRIPTORS),
            description="A part of a fixture (fixture lens) was selected, renamed or deleted, with its axes and whether it selects the whole model.",
        ),
        Signal(
            identifier="@mikro/arraydataset",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ARRAY_DESCRIPTORS),
            description="An image (array dataset) was uploaded or created, changed or deleted, with its axis counts, channel/timepoint extents, value kind and that it is whole.",
        ),
        Signal(
            identifier="@mikro/scene",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(SCENE_DESCRIPTORS),
            description="A scene (a view of layered images and data) was created, changed or deleted, with how it blends its layers and the view it opens in.",
        ),
        Signal(
            identifier="@mikro/chart",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(CHART_DESCRIPTORS),
            description="A chart (a plot of data along one measured axis) was created, changed or deleted, with what its axis measures and its unit.",
        ),
        Signal(
            identifier="@mikro/scenesnapshot",
            kinds=["CREATED", "DELETED"],
            description="A rendered picture (snapshot or thumbnail) of a scene was stored or removed.",
        ),
        Signal(
            identifier="@mikro/tabledataset",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A table of measurements (table dataset) was created, changed or deleted, with what its columns say.",
            descriptors=_keys(TABLE_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/meshcollection",
            kinds=["CREATED", "DELETED"],
            description="A collection of 3D surface meshes was created or deleted.",
            descriptors=_keys(MESH_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/fixture",
            kinds=["CREATED", "DELETED"],
            description="A fixture (a coverslip, a dish, a holder) was created or deleted.",
            descriptors=_keys(FIXTURE_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/networkcollection",
            kinds=["CREATED", "DELETED"],
            description="A collection of node/edge networks (skeletons, vessel trees, tracking graphs) was created or deleted.",
            descriptors=_keys(NETWORK_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/file",
            kinds=["CREATED", "DELETED"],
            descriptors=_keys(FILE_DESCRIPTORS),
            description="A file was uploaded or deleted, with its media type, extension and size.",
        ),
        Signal(
            identifier="@mikro/folder",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A folder of files was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/annotationcollection",
            kinds=["CREATED", "DELETED"],
            description="A collection of hand-drawn annotations (regions of interest) was created or deleted.",
            descriptors=_keys(ANNOTATION_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/annotation",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ANNOTATION_SHAPE_DESCRIPTORS),
            description="An annotation (a hand-drawn region of interest) was drawn, changed or deleted, with the shape it is drawn as and the axes of its space.",
        ),
        Signal(
            identifier="@mikro/metric",
            kinds=["CREATED", "DELETED"],
            descriptors=_keys(METRIC_DESCRIPTORS),
            description="A metric (one measurement) was recorded or deleted. Sent for a metric recorded on its own (createMetric), not for each of a batch (createMetrics): the change feed is where every metric is found.",
        ),
        Signal(
            identifier="@mikro/animation",
            kinds=["CREATED", "DELETED"],
            description="An animation (a camera tour through a scene) was created or deleted.",
        ),
        Signal(
            identifier="@mikro/sparsedataset",
            kinds=["CREATED", "DELETED"],
            description="A sparse matrix (sparse dataset), such as a cell-by-gene count matrix, was created or deleted.",
            descriptors=_keys(SPARSE_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/coordinatesystem",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A coordinate system (a pixel grid, physical or world space) was created, changed or deleted.",
            descriptors=_keys(COORDINATE_SYSTEM_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/transformation",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A transformation between two coordinate systems (a registration, scaling or placement) was created, refined or deleted.",
            descriptors=_keys(TRANSFORMATION_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/coordinateanchor",
            kinds=["CREATED", "DELETED"],
            description="A coordinate anchor, where metadata is pinned, was placed into a dataset or removed.",
            descriptors=_keys(COORDINATE_ANCHOR_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/filelink",
            kinds=["CREATED", "DELETED"],
            description="An uploaded file was linked to the dataset holding its data (imported from it or exported to it), or the link was removed.",
            descriptors=_keys(FILE_LINK_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/metaschema",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A JSON schema for file metadata was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/bigfilestore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The storage of an uploaded file (big file store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/mediastore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The storage of a picture or other media (media store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/zarrstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The zarr storage of image pixels (zarr store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/parquetstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The parquet storage of a table (parquet store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/fabriksstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The storage of a mesh collection (fabriks store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/kulissestore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The storage of a device or scene model (kulisse store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/konnektionstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The storage of a network collection (konnektion store) was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/sparsestore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="The zarr storage of a sparse matrix (sparse store) was requested, filled or deleted.",
        ),
    ],
)


def render(facts: Facts) -> dict[str, JSON]:
    """This release's config for the hub ``facts`` describes."""
    document: dict[str, JSON] = blocks.server(facts)
    document["datalayer"] = blocks.datalayer(facts, "media", "zarr", "parquet", "bigfile")
    document["instance"] = blocks.instance(facts)
    hook = blocks.rekuest_hook(facts)
    if hook is not None:
        document["rekuest_hook"] = hook
    return document


contract = Contract(
    description=Description(
        name="mikro",
        identifier="live.arkitekt.mikro",
        summary="Images and their metadata.",
        needs=Needs(scopes=SCOPES, roles=ROLES, storage=["media", "zarr", "parquet", "bigfile", "fabriks", "konnektion", "kulisse"], instance_key=True, peers=["rekuest"]),
        # No `rekuest_hook`: the hook agent (`mikro_server.hook_agent`) has no action in this
        # release, and an agent with nothing to do is not offered to a hub.
        # `feed`: the change feed of metrics, read by kraph with its instance key (internal network only).
        offers=Offers(endpoints={"rekuest_service": "_rekuest/service", "feed": "_feed"}),
        requires={"rekuest": ">=6"},
        hosts=HOSTS,
    ),
    settings=Settings,
    render=render,
    # How this service is started: there is no script beside it. `arkitekt-service serve`
    # (and `debug`) become these, so they get the container's signals themselves.
    serve=Start(("daphne", "-b", "0.0.0.0", "-p", "80", "--websocket_timeout", "-1", "mikro_server.asgi:application")),
    debug=Start(("python", "manage.py", "runserver", "0.0.0.0:80")),
    jobs={
        "ensureadmin": Job(("ensureadmin",), "Create the operator account the config names"),
        "purge_orphaned_stores": Job(("purge_orphaned_stores",), "Delete the stored objects of data that was deleted, after the grace period"),
        "respec_datasets": Job(("respec_datasets",), "Recompute the spec of datasets created before an axis of one position stopped counting"),
        "backfill_parquet_schemas": Job(("backfill_parquet_schemas",), "Record the columns of parquet stores finished before they were recorded"),
        "merge_duplicate_lenses": Job(("merge_duplicate_lenses",), "Normalize stored lens selections, give every container of every kind its whole lens, merge duplicate lenses onto one row, and state every lens' spec"),
        "restore_descriptors": Job(("restore_descriptors",), "Store the descriptors of every row that misses one its structure declares (--all: of every row)"),
    },
    # `merge_duplicate_lenses` runs for every build, before the release serves: a lens row spelled
    # the old way would otherwise escape the lookup `createLens` makes and mint a duplicate.
    # `restore_descriptors` first: rows from before a key existed are given it without announcing
    # anything, so the whole lenses `merge_duplicate_lenses` then mints find their containers described.
    setup=("ensureadmin", "restore_descriptors", "merge_duplicate_lenses"),
)
