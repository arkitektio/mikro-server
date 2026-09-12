"""The searchable edges rooted at one space, shared by both roots that search them.

A scene and a shared space ask different questions -- "where does this layer sit in world"
versus "what is in here at all" -- but both are a BFS over the same structure: the edges
touching one space, plus the scene-independent facts of the datasets seeded into it, closed
over their derivation lineage.

That structure belongs to the *space and its seeds*, not to whoever is asking, so it lives
here once and :class:`core.logic.scene_graph.SceneGraph` and
:class:`core.logic.space_graph.SpaceGraph` both compose it. What stays with them is what
genuinely differs: a scene answers per layer and returns edges, a space answers per resident
and composes geometry. Before this module the two carried character-identical copies of the
lineage walk, the adjacency assembly and the collection-edge handling.

The adjacency stays **partitioned per dataset**, with one exception. Only the fetch is
batched: an unrelated dataset's edges never enter another's search -- a bucket is the scope
of a search, and merging them would let a BFS return a path through a co-tenant that is
shorter than the truth.

Edges into a shared space are *claims*, a property of the space itself and shared by every
scene over it, so a search simply includes them -- there is no membership to consult. The
dataset buckets carry a dataset's own spatial facts: its levels, lenses, physical spaces
and its derivations.

The exception is **lineage**. A derived dataset -- a deconvolution, a projection, a resample
-- does not sit anywhere on its own account: it sits where the data it was computed from
sits, and the walk to a space runs through that parent's lens, array and intrinsic systems.
Those edges live in the parent's bucket, so the search closes over the chain above a seeded
dataset. A parent is not an unrelated dataset; it is where the pixels came from.
"""

from typing import Iterable

from django.db.models import Q

from core import models
from core.logic import graph as graph_logic

#: Where the per-request root-edge memo lives on the context's loader store.
_ROOT_EDGE_KEY = "space_root_edges"

#: Every edge on a placement path states its own axis order (`Transformation.inputAxes` /
#: `outputAxes`, derived by :func:`core.logic.graph.edge_axis_names`), so the axes of both
#: endpoints ride along with the edges the graph already fetches. Without this the
#: placement query goes back to one query per edge per side.
EDGE_AXIS_PREFETCH = ("input__axes", "output__axes")


def _container_of(system: "models.CoordinateSystem | None", keys: dict[int, tuple]) -> tuple | None:
    """The container a space belongs to, read from a prebuilt map.

    The map is the point. Ownership put the answer in a column on the space; residence puts
    it on the *data*, so it is gathered once for a whole batch by
    :func:`core.logic.graph.container_map` and read here for free. Doing it per system would
    be a query each, which is the N+1 this whole module exists to prevent.

    A collection's space keys to the *collection* -- it used to resolve to None, because the
    map knew only about datasets, and every caller then had to special-case the hole.
    """
    return keys.get(system.pk) if system is not None else None


def _edge_container(edge: "models.Transformation", keys: dict[int, tuple]) -> tuple | None:
    """The container whose data lives in an edge's input space.

    The in-memory mirror of :func:`core.logic.graph.container_q` over the input side. A
    space several containers share resolves to one of them deterministically, so an edge
    still lands in one bucket.
    """
    return _container_of(edge.input, keys)


def _derivation_target(edge: "models.Transformation", keys: dict[int, tuple]) -> tuple | None:
    """The container this edge derives *from*, if it is a derivation edge; else None.

    A derivation edge leaves its container and lands in another one: a deconvolution, a
    projection, a resample stating where its pixels came from. An edge into a shared space
    leaves the container too, but it lands nowhere -- a world belongs to nobody -- so a
    registration is not mistaken for a lineage. Both halves are
    :func:`core.logic.graph.is_derivation_edge`, shared so this and ``derivedFrom`` cannot
    drift apart about what a derivation is.

    An UNMAPPABLE derivation lands in another container and still yields nothing here: this
    closure exists to pull a source's edges into the search, and no search can cross that
    edge to use them. Following it would widen the adjacency with edges the BFS can never
    reach, for a path that does not exist.
    """
    if not graph_logic.is_traversable(edge):
        return None
    if not graph_logic.is_derivation_edge(edge, of_container=_edge_container(edge, keys), keys=keys):
        return None
    return _container_of(edge.output, keys)


def fetch_container_edges(container_keys: set[tuple]) -> list["models.Transformation"]:
    """Every top-level edge whose input system belongs to one of these containers."""
    edges = models.Transformation.objects.filter(parent__isnull=True).filter(graph_logic.container_q(container_keys, field="input"))
    return list(edges.select_related("input", "output").prefetch_related("children", *EDGE_AXIS_PREFETCH))


def container_buckets(seed_keys: set[tuple]) -> tuple[dict[tuple, list["models.Transformation"]], dict[tuple, set[tuple]], set[tuple]]:
    """Every seeded container's own fact edges, closed over its derivation lineage.

    Returns ``(edges by container, parents by container, every container reached)``.

    A derived dataset's placement is not its own fact: it sits where the data it was
    computed from sits, and the walk to a space runs through its source's lens, array and
    intrinsic systems. Those edges live in the source's bucket, so without closing over the
    lineage a search dead-ends the moment it crosses the derivation edge.

    One query per lineage *generation*, not per dataset, which is what keeps a universe flat
    in its source count.

    **A bucket is not a function of its container**, which is why nothing caches these
    across universes. An edge is filed under `_edge_container` and then dropped unless *that*
    container is in the current batch, so a space co-tenanted by A and B files an edge out of
    it under A -- and seeding with B alone drops that edge entirely. Sharing a bucket
    between two differently-seeded universes would silently widen one of their searches.

    **Containers, not datasets.** Keyed on dataset pks, a collection had no key to be filed
    under, so a table or mesh in the lineage closed over nothing and a search that crossed
    into one dead-ended. A table's own edges are now a bucket like any other, which is what
    lets a reconstruction reach world through the localization table it was built from.
    """
    container_keys = set(seed_keys)
    container_edges: dict[tuple, list[models.Transformation]] = {}
    # Every parent, not just one: a fusion derives from several sources, and a path
    # through any of them is a real placement.
    derived_from: dict[tuple, set[tuple]] = {}

    pending = set(container_keys)
    while pending:
        for key in pending:
            container_edges.setdefault(key, [])

        # One container map for the whole generation, then every bucketing decision below is
        # a dict read. There is no fact/claim filter to apply any more (RFC-9): a bucket
        # carries every edge leaving a space its container's data lives in, and which of
        # several routes a placement takes is settled later, by `_bfs_tree`'s widest-path
        # search: the route whose weakest edge is best known wins, hops breaking its ties.
        batch_edges = fetch_container_edges(pending)
        keys = graph_logic.container_map({edge.input_id for edge in batch_edges if edge.input_id} | {edge.output_id for edge in batch_edges if edge.output_id})

        for edge in batch_edges:
            key = _edge_container(edge, keys)
            if key in container_edges:
                container_edges[key].append(edge)
                source = _derivation_target(edge, keys)
                if source is not None:
                    derived_from.setdefault(key, set()).add(source)

        # The ancestors just discovered and not yet fetched. A cycle would be nonsense, but
        # it must not hang the request, so already-seen keys never re-enter.
        pending = {source for sources in derived_from.values() for source in sources if source not in container_edges}
        container_keys |= pending

    return container_edges, derived_from, container_keys


def root_edges_of(space: "models.CoordinateSystem | None", *, organization=None, loaders: dict | None = None) -> list["models.Transformation"]:
    """Every top-level edge touching this space, in both directions.

    Both directions because a registration authored backwards (space -> source) must degrade
    to an inverted step rather than to an unreachable space; top-level only because a
    wrapper's children are steps *within* an edge, not edges of the graph.

    ``organization=None`` means the space's own truth, and that is the answer a scene wants:
    ``CoordinateSystem.registrations`` returns exactly these rows unscoped, so filtering here
    would make ``Layer.pathToWorld`` search a *narrower* set than the field documented as its
    universe. An organization is passed only by a caller that hands whole *containers* back
    to a client and so must not surface a co-tenant's -- see
    :class:`core.logic.space_graph.SpaceGraph`. Both of those are deliberate; neither is the
    other one's bug.

    The memo carries the organization in its key for the same reason: two callers scoping
    differently are not fetching the same rows and must not share one.
    """
    if space is None:
        return []

    key = (space.pk, organization.pk if organization is not None else None)
    memo = loaders.setdefault(_ROOT_EDGE_KEY, {}) if loaders is not None else None
    if memo is not None and key in memo:
        return memo[key]

    query = models.Transformation.objects.filter(Q(output=space) | Q(input=space), parent__isnull=True)
    if organization is not None:
        query = query.filter(organization=organization)
    edges = list(query.select_related("input", "output").prefetch_related("children", *EDGE_AXIS_PREFETCH))

    if memo is not None:
        memo[key] = edges
    return edges


class EdgeUniverse:
    """The edges a search rooted at one space may cross: the space's own, plus its seeds' facts.

    **The seed set is a parameter, and it stays one.** A scene seeds from its two layers; a
    shared space seeds from every resident of every space placeable in it, which can be
    hundreds. Unifying the seeding would make a two-layer scene pay for a hundred-tile stage.

    **Two seed forms, and both are needed.** ``seed_systems`` is for a caller that knows the
    *spaces* it cares about and must be told which datasets live in them; ``seed_datasets``
    is for one that already has the dataset ids off rows in hand. They are not
    interchangeable: :func:`core.logic.graph.residence_map` maps a space to one resident, so
    a hundred tiles sharing one stage space seed a hundred datasets through ``seed_datasets``
    and exactly one through ``seed_systems``.
    """

    def __init__(
        self,
        space: "models.CoordinateSystem | None",
        *,
        organization=None,
        seed_systems: Iterable[int] = (),
        seed_datasets: Iterable[int] = (),
        loaders: dict | None = None,
    ) -> None:
        self.space = space
        self.organization = organization
        # A set, not the caller's iterable: `seed_systems` is read twice below, and an empty
        # generator is truthy. `collection_systems` used to be a second parameter here,
        # because a collection's system needed its own fetch to be resolved at all; under
        # container keys it is an ordinary seed and the distinction is gone.
        seed_systems = set(seed_systems)

        self.root_edges = root_edges_of(space, organization=organization, loaders=loaders)

        # Empty until the seeds resolve, and that order is load-bearing rather than tidy:
        # `keys` widens over the buckets, and the seeds are read *through* it.
        self.container_edges: dict[tuple, list[models.Transformation]] = {}
        self.derived_from: dict[tuple, set[tuple]] = {}
        self.container_keys: set[tuple] = set()

        seeded = seed_systems
        self._keys: dict[int, tuple] = graph_logic.container_map(seeded) if seeded else {}
        # A *copy*, not the same object: `container_of` reads the `keys` property, which
        # widens `_keys_cover` in place -- and `seeded` is iterated below to build the seeds.
        # Aliasing them mutates the set mid-comprehension.
        self._keys_cover: set[int] = set(seeded)

        # A collection used to need its own fetch here: it owns its coordinate system rather
        # than borrowing its dataset's, so the dataset-keyed map had no entry for it and its
        # derivation edge had to be resolved separately and re-filed under the dataset it
        # landed in. Under container keys a collection *is* a bucket, so `container_buckets`
        # fetches its edges like anything else and all of that machinery is gone.
        seeds = {("dataset", dataset_id) for dataset_id in seed_datasets}
        seeds |= {key for system_id in seeded if (key := self.container_of(system_id)) is not None and key[0] != "system"}
        self.container_edges, self.derived_from, self.container_keys = container_buckets(seeds)

        self._adjacency_cache: dict[tuple, dict] = {}

    @property
    def dataset_ids(self) -> set[int]:
        """The array datasets among the containers this universe reached.

        Derived rather than stored: a pyramid level is a dataset's own thing, so the level
        fetch is genuinely dataset-shaped even though the universe is not.
        """
        return {pk for kind, pk in self.container_keys if kind == "dataset"}

    @property
    def keys(self) -> dict[int, tuple]:
        """``{space: container key}`` over the spaces this universe's searchable edges touch.

        Widened once, the first time anyone asks about a space the seed map did not cover --
        a bounded fetch for the whole remainder, never one per space. Moving the widening
        inside a per-system loop is exactly how this becomes a query per layer again;
        ``_keys_cover`` is what forbids it.

        Over the root edges and the buckets. A collection's own system needs no fall-through
        any more: it is a container, so the map has it.
        """
        touched = {edge.input_id for edges in self.container_edges.values() for edge in edges if edge.input_id}
        touched |= {edge.output_id for edges in self.container_edges.values() for edge in edges if edge.output_id}
        touched |= {edge.input_id for edge in self.root_edges if edge.input_id}
        touched |= {edge.output_id for edge in self.root_edges if edge.output_id}

        missing = touched - self._keys_cover
        if missing:
            self._keys.update(graph_logic.container_map(missing))
            self._keys_cover |= missing
        return self._keys

    def container_of(self, system_id: int | None) -> tuple | None:
        """The container whose data lives in a space -- a dataset, or a collection itself."""
        if system_id is None:
            return None
        return self.keys.get(system_id)

    def lineage(self, container_key: tuple) -> list[tuple]:
        """A container and every container it was derived from, transitively, nearest first.

        A fan, not a chain: ``derived_from`` records every parent, because a fusion derives
        from several sources and places through whichever of them is registered. ``sorted``
        makes the order deterministic, and ``seen`` makes a cycle -- nonsense, but it must not
        hang the request -- terminate.
        """
        chain = [container_key]
        seen = {container_key}
        frontier = [container_key]
        while frontier:
            next_frontier: list[int] = []
            for current in frontier:
                for source in sorted(self.derived_from.get(current, ())):
                    if source in seen:
                        continue
                    seen.add(source)
                    chain.append(source)
                    next_frontier.append(source)
            frontier = next_frontier
        return chain

    def adjacency(self, container_key: tuple | None, *, at: dict[str, int] | None = None, admit_scoped: bool = False, require_affine: bool = False) -> dict[int, list[tuple["models.Transformation", bool, int]]]:
        """The searchable universe for one container: its lineage's facts plus this space's claims.

        The partition holds where it matters. An *unrelated* container's edges still never
        enter this search -- that is what stops a BFS wandering out through a co-tenant and
        returning a path shorter than the truth. But a container this one was derived from is
        not unrelated: the path to the space runs straight through it -- and that now
        includes a table a dataset was reconstructed from, not only another dataset.
        """
        # `at` joins the cache key rather than the fetch. Which edges this universe *holds* does
        # not depend on where the caller is standing -- only which of them a search may cross
        # does -- so one fetched universe answers for every coordinate, and asking about two
        # channels in one request costs two dict builds rather than two round trips.
        # `admit_scoped` joins it for the same reason: it selects a different adjacency over
        # the same edges -- the one that answers existence rather than position -- and the two
        # must not share a slot. `require_affine` joins it on the same grounds -- it is a
        # third adjacency over one fetch, the one whose routes are guaranteed to condense.
        cache_key = (container_key, tuple(sorted(at.items())) if at else None, admit_scoped, require_affine)
        if cache_key in self._adjacency_cache:
            return self._adjacency_cache[cache_key]

        edges = list(self.root_edges)
        if container_key is not None:
            for ancestor in self.lineage(container_key):
                edges += self.container_edges.get(ancestor, [])

        adjacency = graph_logic.adjacency_of(edges, at=at, admit_scoped=admit_scoped, require_affine=require_affine)
        self._adjacency_cache[cache_key] = adjacency
        return adjacency
