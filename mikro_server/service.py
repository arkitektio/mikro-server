"""mikro as a service of the hub: the models and the code behind what its contract says it hosts.

What exists here (the structures, the descriptors of their objects, the signals and their kinds)
is declared once, as data, in ``mikro_server.contract`` (``hosts``), so that a hub knows it from the
image. This module only binds it: each structure to its model and to what computes its
descriptors, each signal to the saves and deletes that send it. A structure the contract does not
declare cannot be bound, and one it declares that nothing binds here stops the service at its
start. What a describer computes is stored on the row (``stored_descriptors``) whenever it is saved
or restored; the GraphQL types answer ``descriptors`` from there (``core.types``), and a
``requires`` filter reads it in SQL (``core.logic.descriptor_filter``).

Hosting announces nothing by itself: a structure with no signal below is hosted silently.

That is all a service is. What can be *done* in this process is not declared here: that is an
agent's to say (``mikro_server.hook_agent``), a different thing with its own configuration.
"""


from chart import models as chart_models
from chart.logic import chart as chart_logic
from core import models
from core.descriptors import (
    annotation_descriptors,
    annotation_shape_descriptors,
    coordinate_anchor_descriptors,
    coordinate_system_descriptors,
    dataset_descriptors,
    file_descriptors,
    file_link_descriptors,
    lens_descriptors,
    mesh_descriptors,
    network_descriptors,
    scene_descriptors,
    sparse_descriptors,
    table_descriptors,
    transformation_descriptors,
)
from arkitekt_service.service import Service, organization_of
from arkitekt_service.collaborations import items as collaboration_items
from datalayer import models as datalayer_models
from django.db.models.signals import post_save

from mikro_server.contract import contract

service = Service("mikro", hosts=contract.description.hosts, description="Microscopy data: datasets, their coordinate graph and files.")


# --- Structures: what mikro hosts -----------------------------------------------------

# --- Collaborations: what an object needs to be seen, put in a collaboration along with it ------------------
# What is part of an object (a scene's layers, a dataset's arrays and lenses, a collection's
# annotations) is in a collaboration with it by itself. These name the rest: what it points at to render.

#: The field by which each lens kind names the container it selects from.
_CONTAINERS = ("dataset", "table_dataset", "sparse_dataset", "mesh_collection", "network_collection", "annotation_collection")


def _container_of(lens: models.Lens) -> object:
    """The container a lens selects from: the lens's whole, and what puts it in a collaboration."""
    real = lens.get_real_instance() if hasattr(lens, "get_real_instance") else lens
    return next((getattr(real, name) for name in _CONTAINERS if getattr(real, name, None) is not None), real)


def _scene_needs(scene: models.Scene) -> list[object]:
    """A scene's world, the container behind each layer's lenses, its snapshots' images and its drawing surface."""
    needed: list[object] = [scene.world]
    for layer in scene.layers.select_related("lens", "red_lens", "green_lens", "blue_lens"):
        needed.extend(_container_of(lens) for lens in (layer.lens, layer.red_lens, layer.green_lens, layer.blue_lens) if lens is not None)
    needed.extend(snapshot.store for snapshot in scene.snapshots.select_related("store"))
    needed.extend(models.AnnotationCollection.objects.filter(scene=scene))
    return needed


def _dataset_needs(dataset: models.ArrayDataset) -> list[object]:
    """A dataset's grid, and each array's store and collaboration."""
    arrays = dataset.data_arrays.select_related("store", "coordinate_system")
    return [dataset.coordinate_system, *(array.store for array in arrays), *(array.coordinate_system for array in arrays)]


def _stored_needs(container: object) -> list[object]:
    """A table's, a mesh's or a network's store, and the collaboration it is drawn in."""
    return [getattr(container, "store", None), getattr(container, "coordinate_system", None)]


def _collection_needs(collection: models.AnnotationCollection) -> list[object]:
    """The collaboration a collection's shapes are drawn in, and its bounding boxes'."""
    return [collection.coordinate_system, getattr(collection, "bbox_system", None)]


arraydataset = service.structure(models.ArrayDataset, "@mikro/arraydataset", describe=dataset_descriptors, depends=_dataset_needs)
# One structure per lens kind, so a port typed on pixels is never handed rows. Each kind
# describes itself in its container's vocabulary, over what it selects.
arraylens = service.structure(models.ArrayLens, "@mikro/arraylens", describe=lens_descriptors)
tablelens = service.structure(models.TableLens, "@mikro/tablelens", describe=lens_descriptors)
sparselens = service.structure(models.SparseLens, "@mikro/sparselens", describe=lens_descriptors)
meshlens = service.structure(models.MeshLens, "@mikro/meshlens", describe=lens_descriptors)
networklens = service.structure(models.NetworkLens, "@mikro/networklens", describe=lens_descriptors)
annotationlens = service.structure(models.AnnotationLens, "@mikro/annotationlens", describe=lens_descriptors)
scene = service.structure(models.Scene, "@mikro/scene", describe=scene_descriptors, depends=_scene_needs)


def _chart_descriptors(chart: chart_models.Chart) -> dict[str, str]:
    """A chart's descriptors: what its one axis measures, and in what unit."""
    axis = chart_logic.chart_axis(chart.world)
    return {"@mikro/axis_type": str(axis.type), "@mikro/axis_unit": str(axis.unit)}


chart = service.structure(chart_models.Chart, "@mikro/chart", describe=_chart_descriptors)
scenesnapshot = service.structure(models.SceneSnapshot, "@mikro/scenesnapshot")
tabledataset = service.structure(models.TableDataset, "@mikro/tabledataset", describe=table_descriptors, depends=_stored_needs)
meshcollection = service.structure(models.MeshCollection, "@mikro/meshcollection", describe=mesh_descriptors, depends=_stored_needs)
networkcollection = service.structure(models.NetworkCollection, "@mikro/networkcollection", describe=network_descriptors, depends=_stored_needs)
file = service.structure(models.File, "@mikro/file", describe=file_descriptors)
folder = service.structure(models.Folder, "@mikro/folder")
annotationcollection = service.structure(models.AnnotationCollection, "@mikro/annotationcollection", describe=annotation_descriptors, depends=_collection_needs)
annotation = service.structure(models.Annotation, "@mikro/annotation", describe=annotation_shape_descriptors)
animation = service.structure(models.Animation, "@mikro/animation")
sparsedataset = service.structure(models.SparseDataset, "@mikro/sparsedataset", describe=sparse_descriptors, depends=_stored_needs)
# The coordinate graph, the links and schemas beside the data, and the stores under it. Per
# concrete store kind, for the reason a lens is: `post_save` never fires for the polymorphic base.
coordinatesystem = service.structure(models.CoordinateSystem, "@mikro/coordinatesystem", describe=coordinate_system_descriptors)
transformation = service.structure(models.Transformation, "@mikro/transformation", describe=transformation_descriptors)
coordinateanchor = service.structure(models.CoordinateAnchor, "@mikro/coordinateanchor", describe=coordinate_anchor_descriptors)
filelink = service.structure(models.FileLink, "@mikro/filelink", describe=file_link_descriptors)
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
# A shape carries no organization of its own: it is its collection's.
service.model_signal(annotation, organization=organization_of("collection.organization"))
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


# Cascading keys that point at something shared, not at a whole: a layer is its scene's, not part
# of the lens it draws through (many scenes draw one lens); an edge is its input's, not part of the
# space it places that input in (a world many datasets are registered into).
service.not_part_of(models.Layer, "lens")
service.not_part_of(chart_models.ChartLayer, "lens")
service.not_part_of(models.Transformation, "output")


def _layer_saved(sender: object, instance: models.Layer, raw: bool = False, **_: object) -> None:
    """A layer added to (or changed in) a scene in a collaboration brings what it draws into that collaboration."""
    if not raw:
        collaboration_items.refresh(instance.scene)


post_save.connect(_layer_saved, sender=models.Layer, dispatch_uid="mikro-collaborations-layer")
