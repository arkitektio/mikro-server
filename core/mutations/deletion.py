"""``deleteDerived``: delete everything made from one object and keep the object; see :func:`core.logic.deletion.delete_derived`."""

import strawberry
from kante.types import Info

from core.logic import deletion as deletion_logic
from core.logic import lineage as lineage_logic
from core.mutations._generic import self_owner
from core.scoping import get_for_org
from core.types.lineage import LineageNodeKind


@strawberry.input(description="What to delete the derived data of: one object the lineage walk can stand on, by kind and id")
class DeleteDerivedInput:
    kind: LineageNodeKind = strawberry.field(description="What it is")
    id: strawberry.ID = strawberry.field(description="Its ID, as its own query takes it")


def delete_derived(info: Info, input: DeleteDerivedInput) -> list[strawberry.ID]:
    """Delete what was made from ``input``'s object, transitively; the object stays."""
    kind = LineageNodeKind(input.kind).value
    target = get_for_org(lineage_logic.model_of(kind), info, id=input.id)
    deleted = deletion_logic.delete_derived(info, target, kind, owner=self_owner)
    return [strawberry.ID(f"{key_kind}:{pk}") for key_kind, pk in deleted]


__all__ = ["DeleteDerivedInput", "delete_derived"]
