"""``meshObject``: one object of a mesh collection, to ask what was made from it."""

import strawberry
from kante.types import Info

from core import models, types
from core.scoping import get_for_org

__all__ = ["mesh_object"]


def mesh_object(info: Info, collection: strawberry.ID, object: int) -> types.MeshObject:
    """The object `object` of `collection`, org-scoped through its collection.

    The catalog is not read: an id the catalog does not hold simply has nothing derived from
    it, which is the same answer a real object with no derivations gives.
    """
    resolved = get_for_org(models.MeshCollection, info, id=collection)
    return types.MeshObject(object_id=object, _collection=resolved)
