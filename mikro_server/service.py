"""mikro as the hub's rekuest sees it (vendored ``rekuest_service``): the service, and its HookAgent.

Two declarations, read by rekuest from one manifest and mounted by ``urls.py`` (``*service.urls``):

* the **service** says what exists: the structures mikro hosts, the descriptors of their objects,
  and — every save and delete being announced, with no emit in the mutations — the signals it
  emits. Hub-wide; users' triggers are checked against the kinds and descriptor keys declared here,
  and the GraphQL types answer ``descriptors`` from the same declarations (``core.types``);
* its **agent** says what can be done: the actions rekuest runs here. Every organization has the
  agent and its own schedules, so an action does one organization's share of the work.

Nothing here loops: each run is one pass rekuest started, and a lost run is followed by the next.
"""

from django.conf import settings

from core import models
from core.descriptors import ARRAY_DESCRIPTORS, dataset_descriptors, lens_descriptors
from embeddings import engine
from embeddings.healer import reembed_all
from rekuest_service import Descriptor, HookAgent, Service, organization_of

service = Service("mikro", description="Microscopy data: datasets, their coordinate graph and files.")

# The models whose name + description are embedded (see ``embeddings.healer``).
_EMBEDDED_MODELS = (models.Folder, models.ArrayDataset, models.TableDataset)


# --- Structures ---------------------------------------------------------------------------

ALL = ("CREATED", "UPDATED", "DELETED")
CREATED_DELETED = ("CREATED", "DELETED")
org = organization_of()

service.structure(
    models.ArrayDataset,
    "@mikro/arraydataset",
    kinds=ALL,
    organization=org,
    descriptors=ARRAY_DESCRIPTORS,
    describe=dataset_descriptors,
    description="A multi-dimensional array dataset: its pyramid of arrays and the pixel grid they live in.",
    signal_description="An array dataset was created, changed or deleted, with its axis counts and channel/timepoint extents.",
)
service.structure(
    models.Lens,
    "@mikro/lens",
    # Hosted, not signalled: a lens is a way of looking at a dataset, and it is the dataset whose arrival is news.
    kinds=(),
    descriptors=ARRAY_DESCRIPTORS,
    describe=lens_descriptors,
    description="A selection over an array dataset: what an action that works on pixels is handed.",
)
service.structure(
    models.Scene,
    "@mikro/scene",
    kinds=ALL,
    organization=org,
    descriptors=(
        Descriptor("@mikro/blending", "STRING", "How its layers are blended"),
        Descriptor("@mikro/preferred_view", "STRING", "The view it asks to be opened in"),
    ),
    describe=lambda scene: {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)},
    description="A renderable composition of layers over a shared world coordinate system.",
    signal_description="A scene (a renderable composition of layers) was created, changed or deleted.",
)
service.structure(
    models.SceneSnapshot,
    "@mikro/scenesnapshot",
    kinds=CREATED_DELETED,
    organization=org,
    description="A pre-rendered picture of a scene.",
    signal_description="A rendered picture of a scene was stored or removed.",
)
service.structure(
    models.TableDataset,
    "@mikro/tabledataset",
    kinds=ALL,
    organization=org,
    description="A parquet-backed table of scientific records.",
    signal_description="A table dataset was created, changed or deleted.",
)
service.structure(
    models.MeshCollection,
    "@mikro/meshcollection",
    kinds=CREATED_DELETED,
    organization=org,
    description="An immutable collection of meshes.",
    signal_description="A mesh collection was created or deleted.",
)
service.structure(
    models.File,
    "@mikro/file",
    kinds=CREATED_DELETED,
    organization=org,
    descriptors=(Descriptor("@mikro/content_type", "STRING", "Its media type, empty when unknown"),),
    describe=lambda file: {"@mikro/content_type": file.content_type or ""},
    description="A file in its original format, as it was uploaded.",
    signal_description="A file was uploaded or deleted.",
)
service.structure(
    models.Folder,
    "@mikro/folder",
    kinds=ALL,
    organization=org,
    description="A folder: where a user files the things mikro stores.",
    signal_description="A folder was created, changed or deleted.",
)
service.structure(
    models.AnnotationCollection,
    "@mikro/annotationcollection",
    kinds=CREATED_DELETED,
    organization=org,
    description="A named set of human-drawn annotations.",
    signal_description="An annotation collection was created or deleted.",
)
service.structure(
    models.Animation,
    "@mikro/animation",
    kinds=CREATED_DELETED,
    organization=org,
    description="A named camera tour of a scene.",
    signal_description="An animation (a camera tour of a scene) was created or deleted.",
)
service.structure(
    models.SparseDataset,
    "@mikro/sparsedataset",
    kinds=CREATED_DELETED,
    organization=org,
    description="A sparse dataset.",
    signal_description="A sparse dataset was created or deleted.",
)


# --- The HookAgent ------------------------------------------------------------------------

agent = HookAgent(service)


@agent.action(
    interface="reembed_stale",
    name="Re-embed stale rows",
    description="Re-embed every row of the organization whose vector was produced by another embedding model, or by none.",
    # Scheduled only where embeddings are on; ``embeddings.sweep_interval`` is its cadence.
    default_interval=settings.EMBEDDINGS["SWEEP_INTERVAL"] if engine.enabled() else None,
)
def reembed_stale(organization: str) -> dict:
    """One pass over the organization's embedded rows, in row-locked batches (N replicas may run it at once)."""
    return {"reembedded": reembed_all(_EMBEDDED_MODELS, max_batches=50, organization=organization)}
