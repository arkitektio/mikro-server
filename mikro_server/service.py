"""mikro as a service of the hub: what exists here (vendored ``rekuest_service``).

Two separate declarations, read by rekuest from the service's manifest (``*service.urls`` in
``urls.py``) and catalogued hub-wide:

* the **structures** mikro hosts, and the descriptors of their objects. The GraphQL types answer
  ``descriptors`` from the same declarations (``core.types``);
* the **signals** it emits: which saves and deletes are announced, with no emit in the mutations.
  Users' triggers are checked against the kinds and descriptor keys declared here.

Hosting announces nothing by itself: a structure with no signal below is hosted silently.

That is all a service is. What can be *done* in this process is not declared here: that is an
agent's to say (``mikro_server.hook_agent``), a different thing with its own configuration.
"""


from core import models
from core.descriptors import ARRAY_DESCRIPTORS, dataset_descriptors, lens_descriptors
from rekuest_service import Descriptor, Service, organization_of

service = Service("mikro", description="Microscopy data: datasets, their coordinate graph and files.")


# --- Structures: what mikro hosts -----------------------------------------------------

arraydataset = service.structure(
    models.ArrayDataset,
    "@mikro/arraydataset",
    descriptors=ARRAY_DESCRIPTORS,
    describe=dataset_descriptors,
    description="A multi-dimensional array dataset: its pyramid of arrays and the pixel grid they live in.",
)
# Hosted, never announced: a lens is a way of looking at a dataset, and it is the dataset whose arrival is news.
lens = service.structure(
    models.Lens,
    "@mikro/lens",
    descriptors=ARRAY_DESCRIPTORS,
    describe=lens_descriptors,
    description="A selection over an array dataset: what an action that works on pixels is handed.",
)
scene = service.structure(
    models.Scene,
    "@mikro/scene",
    descriptors=(
        Descriptor("@mikro/blending", "STRING", "How its layers are blended"),
        Descriptor("@mikro/preferred_view", "STRING", "The view it asks to be opened in"),
    ),
    describe=lambda scene: {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)},
    description="A renderable composition of layers over a shared world coordinate system.",
)
scenesnapshot = service.structure(
    models.SceneSnapshot,
    "@mikro/scenesnapshot",
    description="A pre-rendered picture of a scene.",
)
tabledataset = service.structure(
    models.TableDataset,
    "@mikro/tabledataset",
    description="A parquet-backed table of scientific records.",
)
meshcollection = service.structure(
    models.MeshCollection,
    "@mikro/meshcollection",
    description="An immutable collection of meshes.",
)
file = service.structure(
    models.File,
    "@mikro/file",
    descriptors=(Descriptor("@mikro/content_type", "STRING", "Its media type, empty when unknown"),),
    describe=lambda file: {"@mikro/content_type": file.content_type or ""},
    description="A file in its original format, as it was uploaded.",
)
folder = service.structure(
    models.Folder,
    "@mikro/folder",
    description="A folder: where a user files the things mikro stores.",
)
annotationcollection = service.structure(
    models.AnnotationCollection,
    "@mikro/annotationcollection",
    description="A named set of human-drawn annotations.",
)
animation = service.structure(
    models.Animation,
    "@mikro/animation",
    description="A named camera tour of a scene.",
)
sparsedataset = service.structure(
    models.SparseDataset,
    "@mikro/sparsedataset",
    description="A sparse dataset.",
)


# --- Signals: what mikro announces -----------------------------------------------------

ALL = ("CREATED", "UPDATED", "DELETED")
CREATED_DELETED = ("CREATED", "DELETED")
org = organization_of()

service.model_signal(
    arraydataset,
    kinds=ALL,
    organization=org,
    description="An array dataset was created, changed or deleted, with its axis counts and channel/timepoint extents.",
)
service.model_signal(
    scene,
    kinds=ALL,
    organization=org,
    description="A scene (a renderable composition of layers) was created, changed or deleted.",
)
service.model_signal(
    scenesnapshot,
    kinds=CREATED_DELETED,
    organization=org,
    description="A rendered picture of a scene was stored or removed.",
)
service.model_signal(
    tabledataset,
    kinds=ALL,
    organization=org,
    description="A table dataset was created, changed or deleted.",
)
service.model_signal(
    meshcollection,
    kinds=CREATED_DELETED,
    organization=org,
    description="A mesh collection was created or deleted.",
)
service.model_signal(
    file,
    kinds=CREATED_DELETED,
    organization=org,
    description="A file was uploaded or deleted.",
)
service.model_signal(
    folder,
    kinds=ALL,
    organization=org,
    description="A folder was created, changed or deleted.",
)
service.model_signal(
    annotationcollection,
    kinds=CREATED_DELETED,
    organization=org,
    description="An annotation collection was created or deleted.",
)
service.model_signal(
    animation,
    kinds=CREATED_DELETED,
    organization=org,
    description="An animation (a camera tour of a scene) was created or deleted.",
)
service.model_signal(
    sparsedataset,
    kinds=CREATED_DELETED,
    organization=org,
    description="A sparse dataset was created or deleted.",
)
