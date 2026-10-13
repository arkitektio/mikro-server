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


from chart import models as chart_models
from chart.logic import chart as chart_logic
from core import models
from core.descriptors import annotation_descriptors, dataset_descriptors, lens_descriptors, mesh_descriptors, network_descriptors, sparse_descriptors, table_descriptors
from arkitekt_service.service import Service, organization_of
from datalayer import models as datalayer_models

from mikro_server.contract import contract

service = Service("mikro", hosts=contract.description.hosts, description="Microscopy data: datasets, their coordinate graph and files.")


# --- Structures: what mikro hosts -----------------------------------------------------

arraydataset = service.structure(models.ArrayDataset, "@mikro/arraydataset", describe=dataset_descriptors)
# One structure per lens kind, so a port typed on pixels is never handed rows. Each kind
# describes itself in its container's vocabulary, over what it selects.
arraylens = service.structure(models.ArrayLens, "@mikro/arraylens", describe=lens_descriptors)
tablelens = service.structure(models.TableLens, "@mikro/tablelens", describe=lens_descriptors)
sparselens = service.structure(models.SparseLens, "@mikro/sparselens", describe=lens_descriptors)
meshlens = service.structure(models.MeshLens, "@mikro/meshlens", describe=lens_descriptors)
networklens = service.structure(models.NetworkLens, "@mikro/networklens", describe=lens_descriptors)
annotationlens = service.structure(models.AnnotationLens, "@mikro/annotationlens", describe=lens_descriptors)
scene = service.structure(
    models.Scene,
    "@mikro/scene",
    describe=lambda scene: {"@mikro/blending": str(scene.blending), "@mikro/preferred_view": str(scene.preferred_view)},
)


def _chart_descriptors(chart: chart_models.Chart) -> dict[str, str]:
    """A chart's descriptors: what its one axis measures, and in what unit."""
    axis = chart_logic.chart_axis(chart.world)
    return {"@mikro/axis_type": str(axis.type), "@mikro/axis_unit": str(axis.unit)}


chart = service.structure(chart_models.Chart, "@mikro/chart", describe=_chart_descriptors)
scenesnapshot = service.structure(models.SceneSnapshot, "@mikro/scenesnapshot")
tabledataset = service.structure(models.TableDataset, "@mikro/tabledataset", describe=table_descriptors)
meshcollection = service.structure(models.MeshCollection, "@mikro/meshcollection", describe=mesh_descriptors)
networkcollection = service.structure(models.NetworkCollection, "@mikro/networkcollection", describe=network_descriptors)
file = service.structure(models.File, "@mikro/file", describe=lambda file: {"@mikro/content_type": file.content_type or ""})
folder = service.structure(models.Folder, "@mikro/folder")
annotationcollection = service.structure(models.AnnotationCollection, "@mikro/annotationcollection", describe=annotation_descriptors)
animation = service.structure(models.Animation, "@mikro/animation")
sparsedataset = service.structure(models.SparseDataset, "@mikro/sparsedataset", describe=sparse_descriptors)
# The coordinate graph, the links and schemas beside the data, and the stores under it. Per
# concrete store kind, for the reason a lens is: `post_save` never fires for the polymorphic base.
coordinatesystem = service.structure(models.CoordinateSystem, "@mikro/coordinatesystem")
transformation = service.structure(models.Transformation, "@mikro/transformation")
coordinateanchor = service.structure(models.CoordinateAnchor, "@mikro/coordinateanchor")
filelink = service.structure(models.FileLink, "@mikro/filelink")
metaschema = service.structure(models.MetaSchema, "@mikro/metaschema")
bigfilestore = service.structure(datalayer_models.BigFileStore, "@mikro/bigfilestore")
mediastore = service.structure(datalayer_models.MediaStore, "@mikro/mediastore")
zarrstore = service.structure(datalayer_models.ZarrStore, "@mikro/zarrstore")
parquetstore = service.structure(datalayer_models.ParquetStore, "@mikro/parquetstore")
fabriksstore = service.structure(datalayer_models.FabriksStore, "@mikro/fabriksstore")
konnektionstore = service.structure(datalayer_models.KonnektionStore, "@mikro/konnektionstore")
sparsestore = service.structure(datalayer_models.SparseStore, "@mikro/sparsestore")


# --- Signals: what mikro announces -----------------------------------------------------

org = organization_of()

service.model_signal(arraydataset, organization=org)
# Per concrete kind: `post_save` never fires for the polymorphic base. A whole lens is
# announced with its container (it is the handle an action is given for it); a crop or a
# window on its own, with `@mikro/is_whole` false.
service.model_signal(arraylens, organization=org)
service.model_signal(tablelens, organization=org)
service.model_signal(sparselens, organization=org)
service.model_signal(meshlens, organization=org)
service.model_signal(networklens, organization=org)
service.model_signal(annotationlens, organization=org)
service.model_signal(scene, organization=org)
service.model_signal(chart, organization=org)
service.model_signal(scenesnapshot, organization=org)
service.model_signal(tabledataset, organization=org)
service.model_signal(meshcollection, organization=org)
service.model_signal(networkcollection, organization=org)
service.model_signal(file, organization=org)
service.model_signal(folder, organization=org)
service.model_signal(annotationcollection, organization=org)
service.model_signal(animation, organization=org)
service.model_signal(sparsedataset, organization=org)
service.model_signal(coordinatesystem, organization=org)
service.model_signal(transformation, organization=org)
service.model_signal(coordinateanchor, organization=org)
service.model_signal(filelink, organization=org)
service.model_signal(metaschema, organization=org)
service.model_signal(bigfilestore, organization=org)
service.model_signal(mediastore, organization=org)
service.model_signal(zarrstore, organization=org)
service.model_signal(parquetstore, organization=org)
service.model_signal(fabriksstore, organization=org)
service.model_signal(konnektionstore, organization=org)
service.model_signal(sparsestore, organization=org)
