"""What this image answers a hub's installer: ``arkitekt-service <verb>`` (see ``arkitekt_service.contract``).

The installer knows the hub; how this release spells its config is written here, with the
settings it is read by. A key renamed in ``configuration.py`` is renamed in :func:`render` in
the same commit, and no installer has to learn of it.
"""

from __future__ import annotations

from arkitekt_service.contract import JSON, Contract, Description, Descriptor, Facts, Hosts, Job, Needs, Offers, Scope, Signal, Start, Structure, blocks

from mikro_server.configuration import Settings
from mikro_server.vocabulary import ARRAY_DESCRIPTORS, PROVENANCE_DESCRIPTORS

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

#: What an array dataset and a lens carry: what is computed from their axes, and what is only stated.
ARRAY_STRUCTURE_DESCRIPTORS = [*ARRAY_DESCRIPTORS, *PROVENANCE_DESCRIPTORS]

#: What exists on a hub because this service is there: said here, as data, so the hub knows it from
#: the image. ``service.py`` binds each of these to its model and refuses anything not said here.
HOSTS = Hosts(
    structures=[
        Structure(
            identifier="@mikro/arraydataset",
            label="Array Dataset",
            description="A multi-dimensional array dataset: its pyramid of arrays and the pixel grid they live in.",
            descriptors=ARRAY_STRUCTURE_DESCRIPTORS,
        ),
        Structure(
            identifier="@mikro/lens",
            label="Lens",
            description="A selection over an array dataset: what an action that works on pixels is handed.",
            descriptors=ARRAY_STRUCTURE_DESCRIPTORS,
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
        ),
        Structure(
            identifier="@mikro/meshcollection",
            label="Mesh Collection",
            description="An immutable collection of meshes.",
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
        ),
    ],
    signals=[
        Signal(
            identifier="@mikro/arraydataset",
            kinds=["CREATED", "UPDATED", "DELETED"],
            descriptors=[descriptor.key for descriptor in ARRAY_STRUCTURE_DESCRIPTORS],
            description="An array dataset was created, changed or deleted, with its axis counts and channel/timepoint extents.",
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
        ),
        Signal(
            identifier="@mikro/meshcollection",
            kinds=["CREATED", "DELETED"],
            description="A mesh collection was created or deleted.",
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
        "backfill_default_scenes": Job(("backfill_default_scenes",), "Nominate a default scene for datasets that have none, by the old sole-occupancy rule"),
        "backfill_parquet_schemas": Job(("backfill_parquet_schemas",), "Record the columns of parquet stores finished before they were recorded"),
    },
    setup=("ensureadmin",),
)
