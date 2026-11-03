"""What a delete would cost: :func:`core.logic.deletion.preflight`, as GraphQL."""

import kante
import strawberry

from core.types.lineage import LineageNodeKind


@kante.type(description="One thing a delete would take along: something made from what is deleted, directly or further down")
class DeleteCasualty:
    """One row a delete would take along."""

    key: strawberry.ID = strawberry.field(description="`<kind>:<id>`, as the lineage walk keys it")
    kind: LineageNodeKind = strawberry.field(description="What it is")
    id: strawberry.ID = strawberry.field(description="Its ID, as its own query takes it")
    name: str = strawberry.field(description="Its name")
    depth: int = strawberry.field(description="Hops from what is deleted: 1 for something made from it directly")
    parent: strawberry.ID = strawberry.field(description="The key of what it was made from on its nearest path")


@kante.type(description="Why a delete cannot go ahead: one row it would delete that may not be deleted")
class DeleteBlocker:
    """Why one row of a delete cannot go."""

    key: strawberry.ID = strawberry.field(description="`<kind>:<id>` of the row that blocks")
    kind: LineageNodeKind = strawberry.field(description="What it is")
    id: strawberry.ID = strawberry.field(description="Its ID")
    reason: str = strawberry.field(description="Why: not yours to delete, a guard (a layer draws through it, a picker names it) or a reference that must not be deleted with it")


@kante.type(description="Rows of one kind that go with a delete without being lineage: layers, lenses, axes")
class CascadeCount:
    """Rows of one kind that cascade with a delete."""

    label: str = strawberry.field(description="What they are, singular -- e.g. 'layer', 'annotation lens'")
    count: int = strawberry.field(description="How many")


@kante.type(
    description=(
        "What deleting one object would cost, without deleting it: everything made from it (which a plain delete refuses over, and `cascade: true` takes along), "
        "what would stop the delete, and what else cascades with it"
    )
)
class DeletePreflight:
    """What a delete would cost."""

    target: strawberry.ID = strawberry.field(description="`<kind>:<id>` of what would be deleted")
    casualties: list[DeleteCasualty] = strawberry.field(description="Everything made from it, transitively, nearest first. Non-empty means the delete needs `cascade: true`")
    blockers: list[DeleteBlocker] = strawberry.field(description="What stops the delete, with or without `cascade`. Empty when it may go ahead")
    cascade_counts: list[CascadeCount] = strawberry.field(description="Other rows that would go with it, by kind, e.g. the layers drawing it and its lenses")
    truncated: bool = strawberry.field(description="Whether more was made from it than one delete may take along: refused even with `cascade`; delete from the leaves")
    needs_cascade: bool = strawberry.field(description="Whether a plain delete would be refused because something was made from it")
    deletable: bool = strawberry.field(description="Whether the delete can go ahead -- with `cascade: true` when `needsCascade`")
