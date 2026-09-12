"""Queries over the coordinate graph itself, rather than over one of its nodes."""

import strawberry
from kante.types import Info

from core import models, types
from core.logic import attribute_plans as attribute_plans_logic
from core.logic import graph as graph_logic
from core.logic import join_walk
from core.scoping import get_for_org


def coordinate_graph(
    info: Info,
    coordinate_system: strawberry.ID,
    max_depth: int | None = None,
) -> types.CoordinateGraph:
    """Walk the coordinate graph out from one system and return the subgraph it reaches.

    The list queries answer "which edges are there"; this answers "which edges relate to
    *this*", which no filter can, because relatedness is transitive and a filter is not. It
    is the one traversal that is not scene-scoped: `Layer.pathToWorld` answers where a layer
    sits in the scene it belongs to, whereas this hands back the neighbourhood -- a dataset's
    pixel grid, its pyramid levels, its lenses, its physical spaces, the worlds it is registered
    into, and anything else registered into those.

    It still composes nothing. The subgraph comes back as nodes and directed edges, and
    turning that into a matrix is the client's job, for the same reason it always is: two
    scenes can place the same dataset two different ways.
    """
    root = get_for_org(models.CoordinateSystem, info, id=coordinate_system)

    systems, transformations = graph_logic.traverse(
        root,
        organization=info.context.request.organization,
        max_depth=max_depth,
    )

    return types.CoordinateGraph(root=root, systems=systems, transformations=transformations)


def lineage_graph(
    info: Info,
    coordinate_system: strawberry.ID,
    max_depth: int | None = None,
) -> types.LineageGraph:
    """Walk the derivation edges out from one container and return the provenance component.

    `coordinateGraph` answers "which edges relate to this space"; this answers "where did
    this data come from, and what came out of it" -- and the difference is which edges are
    crossed. That one walks everything touching a space, so a registration drags in every
    other dataset registered into the same world, which is a neighbourhood and not a
    lineage. This walks derivation edges only, in both directions, and hands back
    *containers* rather than spaces: a dataset's grid, its levels and its lenses are one
    node in a provenance story rather than three.

    Rooted at a coordinate system for the same reason `coordinateGraph` and
    `attributePlans` are: every container has one and it is the one identifier that means
    the same thing for a dataset, a table, a mesh and an annotation collection. Pass
    `dataset.intrinsicSystem.id`, `tableDataset.coordinateSystem.id`, and so on.

    It composes nothing, and it is not scene-scoped. Provenance is a fact about the data,
    and two scenes cannot disagree about what a thing was computed from.
    """
    root = get_for_org(models.CoordinateSystem, info, id=coordinate_system)

    nodes, edges = graph_logic.lineage_graph(
        root,
        organization=info.context.request.organization,
        max_depth=max_depth,
    )

    return types.LineageGraph(root=root, nodes=nodes, edges=edges)


def _sample_step(sample: "attribute_plans_logic.SampleSpec") -> types.SampleStep:
    """The concrete sample step for a plan, chosen by the store the builder resolved.

    The store *is* the discriminator -- a zarr means an array a worker samples at a
    coordinate, a fabriks means a collection whose geometry already carries the id -- so
    there is no second field saying which, free to disagree with it.
    """
    shared = {"system": sample.system, "consumes": sample.consumes, "produces": sample.produces, "passthrough": sample.passthrough}
    if isinstance(sample.store, models.FabriksStore):
        return types.MeshSample(store=sample.store, **shared)
    if isinstance(sample.store, models.KonnektionStore):
        return types.NetworkSample(store=sample.store, **shared)
    return types.ArraySample(store=sample.store, **shared)


def _lookup_step(lookup: "attribute_plans_logic.LookupSpec") -> types.LookupStep:
    """One lookup as the wire carries it -- the landing's and every hop's alike."""
    return types.LookupStep(
        kind=lookup.kind,
        store=lookup.store,
        key_columns=[types.PlanKeyColumn(axis=key.axis, column=key.column) for key in lookup.key_columns],
        attributes=lookup.attributes,
        sparse_array=lookup.sparse_array,
        key_axis=lookup.key_axis,
        key_held=lookup.key_held,
        value_axes=lookup.value_axes,
    )


def _hop(hop: "attribute_plans_logic.HopSpec") -> types.Hop:
    """One hop as the wire carries it. `via` is null on the landing, whose crossing is the edge."""
    return types.Hop(
        index=hop.index,
        parent=hop.parent,
        cardinality=hop.cardinality,
        via=None if hop.parent is None else types.HopVia(column=hop.via_column, axis=hop.via_axis),
        table=hop.table,
        sparse_dataset=hop.sparse_dataset,
        lookup=_lookup_step(hop.lookup),
        join_path=[types.ColumnOptionJoinStep(table=table, column=column) for table, column in hop.join_path],
    )


def attribute_plans(info: Info, system: strawberry.ID, max_depth: int | None = None, max_join_depth: int = 1) -> list[types.AttributePlan]:
    """Every attribute plan reachable from one system: one per FIELD edge landing on a table or a matrix.

    The server returns a plan; a worker executes it. The plan names the array to sample,
    the axes to sample it on, the parquet to query and the columns to select -- and takes
    no coordinate, so a client fetches it once and executes it per hover, locally, against
    the mask chunks it is already rendering. Plans are discovered across the fact
    component -- probe a source image and the derived instance mask's plans come back,
    each carrying the `path` of steps to its root -- but never through a registration.
    Scene-independent by construction, like `coordinateGraph`: a table's space is not
    scene-owned, so no `scene:` argument exists to be wrong about.

    `max_join_depth` bounds the chain each plan carries past its landing, clamped to
    :data:`core.logic.join_walk.MAX_JOIN_DEPTH`; zero returns the landing alone.
    """
    root = get_for_org(models.CoordinateSystem, info, id=system)
    specs = attribute_plans_logic.build_attribute_plans(
        root,
        organization=info.context.request.organization,
        max_depth=max_depth,
        max_join_depth=max(0, min(max_join_depth, join_walk.MAX_JOIN_DEPTH)),
    )

    return [
        types.AttributePlan(
            edge=spec.edge,
            path=[types.PlacementStep(transformation=step.edge, inverted=step.inverted) for step in spec.path],
            sample=_sample_step(spec.sample),
            hops=[_hop(hop) for hop in spec.hops],
        )
        for spec in specs
    ]
