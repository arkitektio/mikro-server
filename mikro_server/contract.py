"""What this image answers a hub's installer: ``arkitekt-service <verb>`` (see ``arkitekt_service.contract``).

The installer knows the hub; how this release spells its config is written here, with the
settings it is read by. A key renamed in ``configuration.py`` is renamed in :func:`render` in
the same commit, and no installer has to learn of it.
"""

from __future__ import annotations

from arkitekt_service.contract import JSON, Contract, Description, Descriptor, Facts, Hosts, Job, Needs, Offers, Scope, Signal, Start, Structure, blocks

from mikro_server.configuration import Settings
from mikro_server.vocabulary import ANNOTATION_DESCRIPTORS, ARRAY_DESCRIPTORS, MESH_DESCRIPTORS, NETWORK_DESCRIPTORS, SPARSE_DESCRIPTORS, TABLE_DESCRIPTORS

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
            identifier="@mikro/lens",
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
            descriptors=[
                Descriptor(key="@mikro/blending", type="STRING", description="How its layers are blended"),
                Descriptor(key="@mikro/preferred_view", type="STRING", description="The view it asks to be opened in"),
            ],
        ),
        Structure(
            identifier="@mikro/chart",
            label="Chart",
            description="A composition of data laid out along one metric axis, with values read off it.",
            descriptors=[
                Descriptor(key="@mikro/axis_type", type="STRING", description="What its axis measures: SPACE, TIME, MICROTIME or SPECTRUM"),
                Descriptor(key="@mikro/axis_unit", type="STRING", description="The unit of its axis"),
            ],
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
            descriptors=[
                Descriptor(key="@mikro/content_type", type="STRING", description="Its media type, empty when unknown"),
            ],
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
    ],
    signals=[
        # A lens of every kind is announced: a crop or a window is an event an action may wait
        # for, and the handle it is then given. `@mikro/is_whole` is how a trigger tells a crop
        # from the whole lens a container is born with.
        Signal(
            identifier="@mikro/lens",
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
            descriptors=["@mikro/blending", "@mikro/preferred_view"],
            description="A scene (a renderable composition of layers) was created, changed or deleted.",
        ),
        Signal(
            identifier="@mikro/chart",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=["@mikro/axis_type", "@mikro/axis_unit"],
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
            descriptors=["@mikro/content_type"],
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
        offers=Offers(endpoints={"rekuest_service": "_rekuest/service"}),
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
        "merge_duplicate_lenses": Job(("merge_duplicate_lenses",), "Normalize stored lens selections, give every container of every kind its whole lens, and merge duplicate lenses onto one row"),
    },
    # `merge_duplicate_lenses` runs for every build, before the release serves: a lens row spelled
    # the old way would otherwise escape the lookup `createLens` makes and mint a duplicate.
    setup=("ensureadmin", "merge_duplicate_lenses"),
)
