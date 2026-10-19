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
            label="Array Dataset",
            description="A multi-dimensional array dataset: its pyramid of arrays and the pixel grid they live in.",
            descriptors=ARRAY_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/arraylens",
            label="Array Lens",
            description="A selection over an array dataset, by index slices: what an action that works on pixels is handed.",
            descriptors=ARRAY_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/tablelens",
            label="Table Lens",
            description="A selection over a table dataset, by value windows on its coordinate columns: what an action that works on rows is handed.",
            descriptors=TABLE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/sparselens",
            label="Sparse Lens",
            description="A selection over a sparse dataset, by windows on its axes.",
            descriptors=SPARSE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/meshlens",
            label="Mesh Lens",
            description="A selection over a mesh collection, by windows in its own space.",
            descriptors=MESH_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/networklens",
            label="Network Lens",
            description="A selection over a network collection, by windows in its own space.",
            descriptors=NETWORK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/annotationlens",
            label="Annotation Lens",
            description="A selection over an annotation collection, by windows in its drawing space.",
            descriptors=ANNOTATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/scene",
            label="Scene",
            description="A renderable composition of layers over a shared world coordinate system.",
            descriptors=SCENE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/chart",
            label="Chart",
            description="A composition of data laid out along one metric axis, with values read off it.",
            descriptors=CHART_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/scenesnapshot",
            label="Scene Snapshot",
            description="A pre-rendered picture of a scene.",
        ),
        Structure(
            identifier="@mikro/tabledataset",
            label="Table Dataset",
            description="A parquet-backed table of scientific records.",
            descriptors=TABLE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/meshcollection",
            label="Mesh Collection",
            description="An immutable collection of meshes.",
            descriptors=MESH_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/networkcollection",
            label="Network Collection",
            description="An immutable collection of nodes and edges with positions.",
            descriptors=NETWORK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/file",
            label="File",
            description="A file in its original format, as it was uploaded.",
            descriptors=FILE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/folder",
            label="Folder",
            description="A folder: where a user files the things mikro stores.",
        ),
        Structure(
            identifier="@mikro/annotationcollection",
            label="Annotation Collection",
            description="A named set of human-drawn annotations.",
            descriptors=ANNOTATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/annotation",
            label="Annotation",
            description="One human-drawn shape in an annotation collection.",
            descriptors=ANNOTATION_SHAPE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/metric",
            label="Metric",
            description="One value recorded about an annotation or a dataset, by an app or a person: a quantity in a unit, or a code a process emitted. Immutable; a correction supersedes it.",
            descriptors=METRIC_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/animation",
            label="Animation",
            description="A named camera tour of a scene.",
        ),
        Structure(
            identifier="@mikro/sparsedataset",
            label="Sparse Dataset",
            description="A sparse dataset.",
            descriptors=SPARSE_DESCRIPTORS,
        ),
        # The coordinate graph, the links and schemas beside the data, and the stores under it:
        # hosted and announced like everything else, with nothing to describe beyond themselves.
        Structure(
            identifier="@mikro/coordinatesystem",
            label="Coordinate System",
            description="A coordinate space: a node of the coordinate graph, in which data lives and between which transformations map.",
            descriptors=COORDINATE_SYSTEM_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/transformation",
            label="Transformation",
            description="A directed edge of the coordinate graph: a map from one coordinate system into another.",
            descriptors=TRANSFORMATION_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/coordinateanchor",
            label="Coordinate Anchor",
            description="A position in a dataset that metadata is pinned to.",
            descriptors=COORDINATE_ANCHOR_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/filelink",
            label="File Link",
            description="A file and a container holding the same data, and which of the two was made from the other.",
            descriptors=FILE_LINK_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/metaschema",
            label="Meta Schema",
            description="A JSON schema that unstructured metadata is written against.",
        ),
        Structure(
            identifier="@mikro/bigfilestore",
            label="Big File Store",
            description="The stored bytes of a file in its original format.",
        ),
        Structure(
            identifier="@mikro/mediastore",
            label="Media Store",
            description="The stored bytes of a picture or other media object.",
        ),
        Structure(
            identifier="@mikro/zarrstore",
            label="Zarr Store",
            description="The stored zarr array behind an array dataset.",
        ),
        Structure(
            identifier="@mikro/parquetstore",
            label="Parquet Store",
            description="The stored parquet file behind a table dataset.",
        ),
        Structure(
            identifier="@mikro/fabriksstore",
            label="Fabriks Store",
            description="The stored octree of surfaces behind a mesh collection.",
        ),
        Structure(
            identifier="@mikro/konnektionstore",
            label="Konnektion Store",
            description="The stored octree of node/edge networks behind a network collection.",
        ),
        Structure(
            identifier="@mikro/sparsestore",
            label="Sparse Store",
            description="The stored zarr group behind a sparse dataset.",
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
            description="A selection over an array dataset was made, renamed or deleted, with its axis counts, extents, value kind and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/tablelens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(TABLE_DESCRIPTORS),
            description="A selection over a table dataset was made, renamed or deleted, with what its schema says and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/sparselens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(SPARSE_DESCRIPTORS),
            description="A selection over a sparse dataset was made, renamed or deleted, with its rank, its layouts and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/meshlens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(MESH_DESCRIPTORS),
            description="A selection over a mesh collection was made, renamed or deleted, with its axes and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/networklens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(NETWORK_DESCRIPTORS),
            description="A selection over a network collection was made, renamed or deleted, with its axes, whether nodes carry attributes and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/annotationlens",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ANNOTATION_DESCRIPTORS),
            description="A selection over an annotation collection was made, renamed or deleted, with how many shapes it holds and whether it selects everything.",
        ),
        Signal(
            identifier="@mikro/arraydataset",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ARRAY_DESCRIPTORS),
            description="An array dataset was created, changed or deleted, with its axis counts, channel/timepoint extents, value kind and that it is whole.",
        ),
        Signal(
            identifier="@mikro/scene",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(SCENE_DESCRIPTORS),
            description="A scene (a renderable composition of layers) was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/chart",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(CHART_DESCRIPTORS),
            description="A chart (data laid out along one metric axis) was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/scenesnapshot",
            kinds=["CREATED", "DELETED"],
            description="A rendered picture of a scene was stored or removed.",
        ),
        Signal(
            identifier="@mikro/tabledataset",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A table dataset was created, changed or deleted.",
            descriptors=_keys(TABLE_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/meshcollection",
            kinds=["CREATED", "DELETED"],
            description="A mesh collection was created or deleted.",
            descriptors=_keys(MESH_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/networkcollection",
            kinds=["CREATED", "DELETED"],
            description="A network collection was created or deleted.",
            descriptors=_keys(NETWORK_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/file",
            kinds=["CREATED", "DELETED"],
            descriptors=_keys(FILE_DESCRIPTORS),
            description="A file was uploaded or deleted.",
        ),
        Signal(
            identifier="@mikro/folder",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A folder was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/annotationcollection",
            kinds=["CREATED", "DELETED"],
            description="An annotation collection was created or deleted.",
            descriptors=_keys(ANNOTATION_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/annotation",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=_keys(ANNOTATION_SHAPE_DESCRIPTORS),
            description="An annotation was drawn, changed or deleted, with the shape it is drawn as and the axes of its space.",
        ),
        Signal(
            identifier="@mikro/metric",
            kinds=["CREATED", "DELETED"],
            descriptors=_keys(METRIC_DESCRIPTORS),
            description="A metric was recorded or deleted. Sent for a metric recorded on its own (createMetric), not for each of a batch (createMetrics): the change feed is where every metric is found.",
        ),
        Signal(
            identifier="@mikro/animation",
            kinds=["CREATED", "DELETED"],
            description="An animation (a camera tour of a scene) was created or deleted.",
        ),
        Signal(
            identifier="@mikro/sparsedataset",
            kinds=["CREATED", "DELETED"],
            description="A sparse dataset was created or deleted.",
            descriptors=_keys(SPARSE_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/coordinatesystem",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A coordinate system was created, changed or deleted.",
            descriptors=_keys(COORDINATE_SYSTEM_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/transformation",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A transformation between two coordinate systems was created, refined or deleted.",
            descriptors=_keys(TRANSFORMATION_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/coordinateanchor",
            kinds=["CREATED", "DELETED"],
            description="A coordinate anchor was pinned into a dataset or removed.",
            descriptors=_keys(COORDINATE_ANCHOR_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/filelink",
            kinds=["CREATED", "DELETED"],
            description="A file was linked to the container holding its data, or the link was removed.",
            descriptors=_keys(FILE_LINK_DESCRIPTORS),
        ),
        Signal(
            identifier="@mikro/metaschema",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A meta schema was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/bigfilestore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A big file store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/mediastore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A media store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/zarrstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A zarr store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/parquetstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A parquet store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/fabriksstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A fabriks store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/konnektionstore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A konnektion store was requested, filled or deleted.",
        ),
        Signal(
            identifier="@mikro/sparsestore",
            kinds=["CREATED", "UPDATED", "DELETED"],
            description="A sparse store was requested, filled or deleted.",
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
        needs=Needs(scopes=SCOPES, roles=ROLES, storage=["media", "zarr", "parquet", "bigfile", "fabriks", "konnektion"], instance_key=True, peers=["rekuest"]),
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
