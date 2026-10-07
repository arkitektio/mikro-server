"""mikro as a service of the hub: the models and the code behind what its contract says it hosts.

What exists here (the structures, the descriptors of their objects, the signals and their kinds)
is declared once, as data, in ``mikro_server.contract`` (``hosts``), so that a hub knows it from the
image. This module only binds it: each structure to its model and to what computes its
descriptors, each signal to the saves and deletes that send it. A structure the contract does not
declare cannot be bound, and one it declares that nothing binds here stops the service at its
start. The GraphQL types answer ``descriptors`` from the same binding (``core.types``).

Hosting announces nothing by itself: a structure with no signal below is hosted silently.

That is all a service is. What can be *done* in this process is not declared here: that is an
agent's to say (``mikro_server.hook_agent``), a different thing with its own configuration.
"""


from core import models
from core.descriptors import dataset_descriptors, lens_descriptors
from arkitekt_service.service import Service, organization_of

from mikro_server.contract import contract

service = Service("mikro", hosts=contract.description.hosts, description="Microscopy data: datasets, their coordinate graph and files.")


# --- Structures: what mikro hosts -----------------------------------------------------

arraydataset = service.structure(models.ArrayDataset, "@mikro/arraydataset", describe=dataset_descriptors)
# Hosted, never announced: a lens is a way of looking at a dataset, and it is the dataset whose arrival is news.
lens = service.structure(models.Lens, "@mikro/lens", describe=lens_descriptors)
scene = service.structure(
    models.Scene,
    "@mikro/scene",
    describe=lambda scene: {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)},
)
scenesnapshot = service.structure(models.SceneSnapshot, "@mikro/scenesnapshot")
tabledataset = service.structure(models.TableDataset, "@mikro/tabledataset")
meshcollection = service.structure(models.MeshCollection, "@mikro/meshcollection")
file = service.structure(models.File, "@mikro/file", describe=lambda file: {"@mikro/content_type": file.content_type or ""})
folder = service.structure(models.Folder, "@mikro/folder")
annotationcollection = service.structure(models.AnnotationCollection, "@mikro/annotationcollection")
animation = service.structure(models.Animation, "@mikro/animation")
sparsedataset = service.structure(models.SparseDataset, "@mikro/sparsedataset")


# --- Signals: what mikro announces -----------------------------------------------------

org = organization_of()

service.model_signal(arraydataset, organization=org)
service.model_signal(scene, organization=org)
service.model_signal(scenesnapshot, organization=org)
service.model_signal(tabledataset, organization=org)
service.model_signal(meshcollection, organization=org)
service.model_signal(file, organization=org)
service.model_signal(folder, organization=org)
service.model_signal(annotationcollection, organization=org)
service.model_signal(animation, organization=org)
service.model_signal(sparsedataset, organization=org)
