"""``preflightDelete<Model>``: what deleting one object would cost; see :func:`core.logic.deletion.preflight`."""

from collections.abc import Callable

import strawberry
from kante.types import Info

from core import models
from core.logic import deletion as deletion_logic
from core.logic import lineage as lineage_logic
from core.mutations._generic import self_owner
from core.scoping import get_for_org
from core.types.deletion import CascadeCount, DeleteBlocker, DeleteCasualty, DeletePreflight
from core.types.lineage import LineageNodeKind


def _key(kind: str, pk: object) -> str:
    return f"{kind}:{pk}"


def _preflight_for(model: type, kind: str) -> Callable[[Info, strawberry.ID], DeletePreflight]:
    """One model's preflight query: the shared preflight, rooted at a row of ``model``."""

    def resolve(info: Info, id: strawberry.ID) -> DeletePreflight:
        target = get_for_org(model, info, id=id)
        result = deletion_logic.preflight(info, target, kind, self_owner)
        blockers = [DeleteBlocker(key=_key(b.kind, b.pk), kind=LineageNodeKind(b.kind), id=str(b.pk), reason=b.reason) for b in result.blockers]
        return DeletePreflight(
            target=_key(kind, lineage_logic.node_pk(kind, target.pk)),
            casualties=[
                DeleteCasualty(key=_key(c.kind, c.pk), kind=LineageNodeKind(c.kind), id=str(c.pk), name=c.name, depth=c.depth, parent=_key(*c.parent))
                for c in result.plan.casualties
            ],
            blockers=blockers,
            cascade_counts=[CascadeCount(label=label, count=count) for label, count in result.cascade_counts],
            truncated=result.plan.truncated,
            needs_cascade=bool(result.plan.casualties),
            deletable=not blockers and not result.plan.truncated,
        )

    resolve.__name__ = f"preflight_delete_{model.__name__.lower()}"
    return resolve


preflight_delete_array_dataset = _preflight_for(models.ArrayDataset, "dataset")
preflight_delete_table_dataset = _preflight_for(models.TableDataset, "tabledataset")
preflight_delete_sparse_dataset = _preflight_for(models.SparseDataset, "sparsedataset")
preflight_delete_mesh_collection = _preflight_for(models.MeshCollection, "meshcollection")
preflight_delete_network_collection = _preflight_for(models.NetworkCollection, "networkcollection")
preflight_delete_annotation_collection = _preflight_for(models.AnnotationCollection, "annotationcollection")
preflight_delete_fixture = _preflight_for(models.Fixture, "fixture")
preflight_delete_file = _preflight_for(models.File, "file")
preflight_delete_annotation = _preflight_for(models.Annotation, "annotation")

__all__ = [
    "preflight_delete_annotation",
    "preflight_delete_annotation_collection",
    "preflight_delete_array_dataset",
    "preflight_delete_file",
    "preflight_delete_fixture",
    "preflight_delete_mesh_collection",
    "preflight_delete_network_collection",
    "preflight_delete_sparse_dataset",
    "preflight_delete_table_dataset",
]
