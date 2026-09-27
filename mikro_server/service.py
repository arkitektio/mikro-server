"""mikro as the hub's rekuest sees it: the actions it offers, the signals it emits (vendored ``rekuest_service``).

Like an arkitekt ``App``: one ``Service`` declaration, mounted by ``urls.py`` (``*service.urls``),
read by rekuest from the manifest. Nothing here loops: each run is one pass rekuest started, and
a lost run is followed by the next.
"""

from django.conf import settings

from core import models
from embeddings import engine
from embeddings.healer import reembed_all
from core.descriptors import ARRAY_DESCRIPTOR_KEYS, array_descriptors
from rekuest_service import Service, organization_of

service = Service("mikro", description="Microscopy data: datasets, their coordinate graph and files.")

# The models whose name + description are embedded (see ``embeddings.healer``).
_EMBEDDED_MODELS = (models.Folder, models.ArrayDataset, models.TableDataset)


# --- Signals ------------------------------------------------------------------------------
# What mikro announces to the hub's rekuest: every save and delete of these models (a model
# signal each; no emit in the mutations). rekuest reads them from the manifest, so users'
# triggers are checked against the kinds and descriptor keys declared here.

ALL = ("CREATED", "UPDATED", "DELETED")
CREATED_DELETED = ("CREATED", "DELETED")
org = organization_of()


def _dataset_descriptors(dataset: models.ArrayDataset) -> dict:
    """The client's axis vocabulary, from the row (read at commit: the axes are written after it)."""
    try:
        return array_descriptors([axis.type for axis in dataset.axes], dataset.shape_list)
    except ValueError:  # axes and shape disagree (a half-written dataset): no descriptors, still a signal
        return {}


dataset_created = service.model_signal(
    models.ArrayDataset,
    "@mikro/arraydataset",
    kinds=ALL,
    organization=org,
    descriptors=_dataset_descriptors,
    descriptor_keys=ARRAY_DESCRIPTOR_KEYS,
    description="An array dataset was created, changed or deleted, with its axis counts and channel/timepoint extents.",
)
scene_signal = service.model_signal(
    models.Scene,
    "@mikro/scene",
    kinds=ALL,
    organization=org,
    descriptors=lambda scene: {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)},
    descriptor_keys=("@mikro/blending", "@mikro/preferred_view"),
    description="A scene (a renderable composition of layers) was created, changed or deleted.",
)
snapshot_signal = service.model_signal(
    models.SceneSnapshot,
    "@mikro/scenesnapshot",
    kinds=CREATED_DELETED,
    organization=org,
    description="A rendered picture of a scene was stored or removed.",
)
service.model_signal(models.TableDataset, "@mikro/tabledataset", kinds=ALL, organization=org, description="A table dataset was created, changed or deleted.")
service.model_signal(models.MeshCollection, "@mikro/meshcollection", kinds=CREATED_DELETED, organization=org, description="A mesh collection was created or deleted.")
service.model_signal(
    models.File,
    "@mikro/file",
    kinds=CREATED_DELETED,
    organization=org,
    descriptors=lambda file: {"@mikro/content_type": file.content_type or ""},
    descriptor_keys=("@mikro/content_type",),
    description="A file was uploaded or deleted.",
)
service.model_signal(models.Folder, "@mikro/dataset", kinds=ALL, organization=org, description="A folder (dataset) was created, changed or deleted.")
service.model_signal(models.AnnotationCollection, "@mikro/annotationcollection", kinds=CREATED_DELETED, organization=org, description="An annotation collection was created or deleted.")
service.model_signal(models.Animation, "@mikro/animation", kinds=CREATED_DELETED, organization=org, description="An animation (a camera tour of a scene) was created or deleted.")
service.model_signal(models.SparseDataset, "@mikro/sparsedataset", kinds=CREATED_DELETED, organization=org, description="A sparse dataset was created or deleted.")


@service.action(
    interface="reembed_stale",
    name="Re-embed stale rows",
    description="Re-embed every row whose vector was produced by another embedding model, or by none.",
    # Scheduled only where embeddings are on; ``embeddings.sweep_interval`` is its cadence.
    default_interval=settings.EMBEDDINGS["SWEEP_INTERVAL"] if engine.enabled() else None,
)
def reembed_stale() -> dict:
    """One pass over every embedded model, in row-locked batches (N replicas may run it at once)."""
    return {"reembedded": reembed_all(_EMBEDDED_MODELS, max_batches=50)}
