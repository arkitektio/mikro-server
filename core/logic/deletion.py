"""Deletes follow lineage: nothing is deleted while something made from it stands, unless asked to take it along.

A delete of anything the ``lineage`` walk can stand on -- a container, a file, a shape --
first asks what was made from it, transitively: the derived datasets and tables, the data
computed within a shape, the shapes refined or converted from it, the series converted out
of a file, the files exported from a dataset. That closure is :func:`plan`, read off
:func:`core.logic.lineage.descendants`, so a delete and the lineage viewer cannot disagree
about what is downstream.

- Nothing downstream: the delete is what it always was.
- Something downstream and no ``cascade``: refused, naming it. Before this rule, deleting a
  source dropped its children's derivation edges with it (``Transformation`` CASCADEs with its
  spaces) and left them standing with a lineage that said nothing -- the worst of both.
- ``cascade``: every row of the closure is checked as the target is (owner, delete guards),
  all before anything goes, then deleted with the target in one transaction.

``preflight`` answers the same questions without deleting: what would go, what stops it, and
what else cascades (layers, lenses) -- the ``preflightDelete<Model>`` queries.
"""

import dataclasses
from collections.abc import Callable, Iterable

from arkitekt_datalayer import storage
from django.db import router, transaction
from django.db.models import Model, Q, QuerySet
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from kante.types import Info

from core import models
from core.logic import compositions as compositions_logic
from core.logic import lineage as lineage_logic

#: How far a cascade reaches. A closure the walk cuts short is refused rather than half deleted.
MAX_DEPTH = lineage_logic.MAX_DEPTH
MAX_NODES = lineage_logic.MAX_LIMIT

#: The kinds a delete follows lineage for: every node kind the walk can reach.
LINEAGE_KINDS: tuple[str, ...] = ("dataset", "tabledataset", "sparsedataset", "meshcollection", "networkcollection", "annotationcollection", "fixture", lineage_logic.FILE, lineage_logic.ANNOTATION)

CASCADE_DESCRIPTION = (
    "Also delete everything made from it -- the closure `preflightDelete<Model>` lists: derived data, data computed within a shape, shapes refined or converted from it, "
    "series converted out of a file, files exported from a dataset. Without it, a delete of something anything was made from is refused, naming what"
)

NodeKey = tuple[str, int | str]


@dataclasses.dataclass(frozen=True)
class Casualty:
    """One row a delete would take along."""

    kind: str
    pk: int | str
    name: str
    #: Hops from the nearest target.
    depth: int
    #: What it was made from on its nearest path.
    parent: NodeKey


@dataclasses.dataclass
class Plan:
    """What deleting ``targets`` means: everything downstream of them, nearest first."""

    targets: list[NodeKey]
    casualties: list[Casualty]
    #: The walk stopped short -- past ``MAX_DEPTH`` hops, ``MAX_NODES`` rows or a node's fan-out.
    truncated: bool


def _shapes_with_children_elsewhere(collection_pks: Iterable[int]) -> list[NodeKey]:
    """The shapes of these collections that something outside them was made from.

    A collection's shapes go with it by CASCADE, and the walk never fans out from a collection
    to its shapes. Data computed *within* a shape is already a hop from the collection (its
    edge lands in the collection's drawing space); a shape made from one of its shapes in
    another collection is not, so those shapes are walked from as well.
    """
    pks = list(collection_pks)
    if not pks:
        return []
    sources = (
        models.AnnotationDerivation.objects.filter(source__collection_id__in=pks)
        .exclude(child__collection_id__in=pks)
        .values_list("source_id", flat=True)
        .distinct()
    )
    return [(lineage_logic.ANNOTATION, str(pk)) for pk in sources]


def plan(targets: list[NodeKey], *, organization) -> Plan:  # noqa: ANN001 - authentikate's Organization
    """Everything downstream of ``targets``, nearest first, each once.

    Walked again while it finds annotation collections whose shapes something elsewhere was
    made from: each round adds those shapes as roots. A collection's own shapes are never
    casualties -- they go with it -- only what was made from them is.
    """
    targets = [(kind, lineage_logic.node_pk(kind, pk)) for kind, pk in targets]
    roots: list[NodeKey] = list(dict.fromkeys(targets))
    collections_seen: set[int] = set()
    truncated = False

    while True:
        collections = {int(pk) for kind, pk in roots if kind == "annotationcollection"} - collections_seen
        collections_seen |= collections
        extra = [root for root in _shapes_with_children_elsewhere(collections) if root not in roots]
        roots.extend(extra)
        if len(roots) > lineage_logic.MAX_ROOTS:
            return Plan(targets=targets, casualties=[], truncated=True)

        walked = lineage_logic.descendants(roots, organization=organization, down=MAX_DEPTH, limit=MAX_NODES, fanout=lineage_logic.MAX_FANOUT)
        truncated = any(entry.more for entry in walked)
        found: dict[NodeKey, Casualty] = {}
        for entry in walked:
            for node in entry.nodes:
                key = (node.kind, node.pk)
                if key in roots:
                    continue
                known = found.get(key)
                if known is None or node.depth < known.depth:
                    found[key] = Casualty(kind=node.kind, pk=node.pk, name=node.name, depth=node.depth, parent=entry.parents.get(key, entry.root))

        # A casualty collection may hold shapes something elsewhere was made from: walk again.
        new_collections = {int(pk) for kind, pk in found if kind == "annotationcollection"} - collections_seen
        if not new_collections:
            break
        roots.extend(key for key in found if key[0] == "annotationcollection" and int(key[1]) in new_collections)

    # A shape of a collection that is itself going goes with it -- by CASCADE, not as a casualty
    # of its own. What was made from it elsewhere stays listed.
    shapes = [pk for kind, pk in found if kind == lineage_logic.ANNOTATION]
    if shapes and collections_seen:
        inside = {str(pk) for pk in models.Annotation.objects.filter(pk__in=shapes, collection_id__in=collections_seen).values_list("pk", flat=True)}
        found = {key: casualty for key, casualty in found.items() if not (key[0] == lineage_logic.ANNOTATION and key[1] in inside)}
    casualties = sorted(found.values(), key=lambda casualty: (casualty.depth, casualty.kind, str(casualty.pk)))
    return Plan(targets=targets, casualties=casualties, truncated=truncated or len(found) >= MAX_NODES)


def instances(keys: Iterable[NodeKey], organization) -> dict[NodeKey, Model]:  # noqa: ANN001 - authentikate's Organization
    """The rows behind node keys, scoped to the organization: one query per kind."""
    by_kind: dict[str, list[int | str]] = {}
    for kind, pk in keys:
        by_kind.setdefault(kind, []).append(pk)
    found: dict[NodeKey, Model] = {}
    for kind, pks in by_kind.items():
        model = lineage_logic.model_of(kind)
        scope = {"collection__organization": organization} if kind == lineage_logic.ANNOTATION else {"organization": organization}
        for row in model.objects.filter(pk__in=pks, **scope):
            found[(kind, lineage_logic.node_pk(kind, row.pk))] = row
    return found


@dataclasses.dataclass(frozen=True)
class Blocker:
    """Why one row of a delete cannot go."""

    kind: str
    pk: int | str
    reason: str


def _checks(info: Info, rows: dict[NodeKey, Model], owner: Callable[[Model], Iterable[int | None]] | None) -> list[Blocker]:
    """Every row's own refusals: not the caller's to delete, or a delete guard."""
    from core.mutations._generic import assert_can_delete  # the permission half lives with the mutations

    blockers: list[Blocker] = []
    for (kind, pk), row in rows.items():
        checks: list[Callable[[], None]] = [lambda row=row: compositions_logic.assert_deletable(row)]
        if owner is not None:
            checks.insert(0, lambda row=row: assert_can_delete(info, row, owner))
        for check in checks:
            try:
                check()
            except (PermissionError, ValueError) as error:
                blockers.append(Blocker(kind=kind, pk=pk, reason=str(error)))
    return blockers


def _collect(rows: list[Model]) -> tuple[Collector | None, list[str]]:
    """Django's own answer to "what goes with these": the collector, or what PROTECT/RESTRICT stops."""
    if not rows:
        return None, []
    collector = Collector(using=router.db_for_write(type(rows[0])))
    by_model: dict[type, list[Model]] = {}
    for row in rows:
        by_model.setdefault(type(row), []).append(row)
    try:
        # One call per model: the collector takes the rows of one model at a time.
        for group in by_model.values():
            collector.collect(group)
    except (ProtectedError, RestrictedError) as error:
        held = list(error.protected_objects if isinstance(error, ProtectedError) else error.restricted_objects)
        named = ", ".join(f"{type(obj).__name__} {obj.pk}" for obj in held[:5])
        more = f" and {len(held) - 5} more" if len(held) > 5 else ""
        return None, [f"Still referenced by {named}{more}, which may not be deleted with it"]
    return collector, []


@dataclasses.dataclass
class Preflight:
    """What a delete would cost, without deleting."""

    plan: Plan
    blockers: list[Blocker]
    #: Other rows that cascade with the delete, by model, e.g. ("layer", 3).
    cascade_counts: list[tuple[str, int]]


def _counts(collector: Collector, explicit: list[Model]) -> list[tuple[str, int]]:
    """What else the collector would delete, by model (singular label), the explicit rows aside.

    A multi-table child (an annotation lens) is collected with its parent row (a lens): only the
    child is counted, since it is one thing.
    """
    named = {(type(row), row.pk) for row in explicit}
    collected = [model for model in collector.data if model._meta.app_label == "core"]
    parents = {parent for model in collected for parent in model._meta.get_parent_list()}
    counts: dict[str, int] = {}

    def add(model: type, count: int) -> None:
        if count and model._meta.app_label == "core" and model not in parents:
            label = str(model._meta.verbose_name)
            counts[label] = counts.get(label, 0) + count

    for model, objs in collector.data.items():
        add(model, sum(1 for obj in objs if (model, obj.pk) not in named))
    for queryset in collector.fast_deletes:
        add(queryset.model, queryset.count())
    return sorted(counts.items())


def _internal_links(rows: list[Model]) -> "QuerySet[models.AnnotationDerivation]":
    """The shape links whose child goes in this delete.

    A shape is RESTRICT on the shape it was made from, so Django refuses to delete a source
    while its child stands -- the backstop for this whole module. When the child goes in the
    same delete the link is internal, and it is removed first: otherwise the order of the
    deletes, not what is deleted, would decide whether it succeeds.
    """
    shapes = [row.pk for row in rows if isinstance(row, models.Annotation)]
    collections = [row.pk for row in rows if isinstance(row, models.AnnotationCollection)]
    if not shapes and not collections:
        return models.AnnotationDerivation.objects.none()
    return models.AnnotationDerivation.objects.filter(Q(child_id__in=shapes) | Q(child__collection_id__in=collections))


def preflight(info: Info, target: Model, kind: str, owner: Callable[[Model], Iterable[int | None]] | None) -> Preflight:
    """What deleting ``target`` would take along and what stops it, computed and not acted on.

    Run in a transaction that is rolled back: the internal shape links are removed so the
    collector sees what the delete itself would, and nothing of it is kept.
    """
    organization = info.context.request.organization
    key = (kind, lineage_logic.node_pk(kind, target.pk))
    planned = plan([key], organization=organization)
    rows = {key: target, **instances(((c.kind, c.pk) for c in planned.casualties), organization)}
    blockers = _checks(info, rows, owner)

    counts: list[tuple[str, int]] = []
    with transaction.atomic():
        _internal_links(list(rows.values())).delete()
        collector, refused = _collect(list(rows.values()))
        blockers.extend(Blocker(kind=kind, pk=key[1], reason=reason) for reason in refused)
        if collector is not None:
            counts = _counts(collector, list(rows.values()))
        transaction.set_rollback(True)
    return Preflight(plan=planned, blockers=blockers, cascade_counts=counts)


def refusal(planned: Plan) -> str:
    """Why a delete without ``cascade`` was refused, naming what is downstream."""
    shown = ", ".join(f"{casualty.kind} '{casualty.name}'" for casualty in planned.casualties[:5])
    more = f" and {len(planned.casualties) - 5} more" if len(planned.casualties) > 5 else ""
    return (
        f"{len(planned.casualties)} thing(s) were made from it -- {shown}{more} -- and would lose where they came from. "
        "Delete them first, or pass `cascade: true` to delete them with it (`preflightDelete<Model>` lists what that takes)"
    )


def delete_with_lineage(info: Info, target: Model, kind: str, *, cascade: bool, owner: Callable[[Model], Iterable[int | None]] | None, delete: Callable[[Model], None] | None = None) -> None:
    """Delete ``target``, and with ``cascade`` everything made from it -- or refuse.

    The target's own owner and guard checks have already run (``make_delete``). Every casualty
    is held to the same, all before anything is deleted, so a refusal leaves everything in place.
    """
    organization = info.context.request.organization
    planned = plan([(kind, target.pk)], organization=organization)
    if planned.casualties and not cascade:
        raise ValueError(refusal(planned))
    if planned.truncated:
        raise ValueError(
            f"More was made from it than one delete may take along (over {MAX_DEPTH} hops deep or {MAX_NODES} rows). Delete what was made from it in parts, from the leaves"
        )

    casualties = instances(((c.kind, c.pk) for c in planned.casualties), organization)
    blockers = _checks(info, casualties, owner)
    if blockers:
        first = blockers[0]
        raise ValueError(f"Cannot delete {first.kind} {first.pk} along with it: {first.reason}")

    # Farthest first, so a row never outlives what it was made from inside the transaction.
    order = [casualties[(c.kind, c.pk)] for c in sorted(planned.casualties, key=lambda c: -c.depth) if (c.kind, c.pk) in casualties]
    everything = [*order, target]
    with transaction.atomic():
        _internal_links(everything).delete()
        # Collected *before* the delete, because the cascade has to still be walkable, and
        # flagged after it, so a delete that raises flags nothing (see make_delete).
        orphaned = [store for row in everything for store in storage.stores_orphaned_by(row)]
        for row in order:
            if type(row).objects.filter(pk=row.pk).exists():  # a collection deleted earlier took its shapes
                row.delete()
        if delete is None:
            target.delete()
        else:
            delete(target)
        storage.flag_orphaned(orphaned)
