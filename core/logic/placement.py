"""Where a source space sits in a world, answered over the world's edge universe.

Every composition asks the same questions of the same edges: a scene asks where each of its
layers sits in its world, a chart asks where each of its layers sits along its axis. The
questions take a *source space* and a *world* and nothing else -- which composition is
asking, and what it will do with the answer, is not something a path search can depend on.

So they live here, on a class that knows no composition. :class:`core.logic.scene_graph.SceneGraph`
is this plus a scene's layer list and its pyramid levels; another composition builds its own
reader the same way, seeding the universe with the spaces its own layers draw from.

**The universe itself is not this module's either.** It belongs to the world and lives in
:class:`core.logic.edge_universe.EdgeUniverse`.
"""

from collections.abc import Iterable

from core import enums, models
from core.logic import edge_universe
from core.logic import graph as graph_logic


class PlacementGraph:
    """The placement questions of one world, over a universe seeded with the sources asked about."""

    def __init__(self, world: "models.CoordinateSystem | None", *, seed_systems: Iterable[int] = (), loaders: dict | None = None) -> None:
        """Fetch the edge universe rooted at the world and seeded with the given source spaces."""
        self.world = world

        # No organization is passed, and that is deliberate: the world's edges are the
        # space's own truth, which is exactly what `CoordinateSystem.registrations` returns
        # unscoped. Scoping here would make `pathToWorld` search a narrower set than the
        # field that documents itself as its universe. `SpaceGraph` scopes because it hands
        # back whole containers; this one returns edges and systems. See `root_edges_of`.
        self.universe = edge_universe.EdgeUniverse(world, seed_systems=seed_systems, loaders=loaders)

        self._world_axes: list[str] | None = None

    # --- the edge universe, which the space owns -----------------------------

    @property
    def keys(self) -> dict[int, tuple]:
        """``{space: container key}`` over every space this graph's edges touch."""
        return self.universe.keys

    def container_of(self, system: "models.CoordinateSystem | None") -> tuple | None:
        """The container whose data lives in a space -- a dataset, or a collection itself."""
        return self.universe.container_of(system.pk) if system is not None else None

    def adjacency(self, container_key: tuple | None, *, at: dict[str, int] | None = None, admit_scoped: bool = False, require_affine: bool = False) -> dict[int, list[tuple["models.Transformation", bool, int]]]:
        """The searchable edge universe for one container: its lineage's facts plus the world's claims."""
        return self.universe.adjacency(container_key, at=at, admit_scoped=admit_scoped, require_affine=require_affine)

    @property
    def world_axes(self) -> list[str]:
        """The world's axis order, read once per graph rather than once per source.

        `self.world` is one object for the whole composition, so its axes are one fact -- but
        `axes.all()` is a query every time it is asked, and asking it inside a per-layer
        resolver is precisely the growth `test_scene_placements_are_flat_in_layer_count`
        exists to catch.
        """
        if self._world_axes is None:
            self._world_axes = [axis.name for axis in self.world.axes.all()] if self.world else []
        return self._world_axes

    # --- the questions -------------------------------------------------------

    def path(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The path of edges from a source space to this world.

        ``None`` when there is no source or no path; ``[]`` when the source already is the
        world.

        ``at`` is where along the acquisition axes the question is being asked -- ``{"c": 2}``.
        It is a parameter of the *question*, never of the graph, which is why it lives here and
        not on ``__init__``: the universe this searches is the same one whatever coordinate is
        fixed, so two channels asked in one request share every query and differ only in which
        selector-scoped edges the walk may cross. Omitted, no scoped edge is crossed at all.

        **Routes that condense are preferred, in two passes.** The affine-only adjacency is
        searched first and the whole universe only if that finds nothing. Which matters because
        the walk ranks routes by bottleneck validity and then by hops -- invariance is
        deliberately not a key (`graph._bfs_tree`) -- so a one-hop VALIDATED warp field beat a
        two-hop affine chain, and `asAffine` then raised for a layer whose creation gate had
        just found an affine route and accepted it. A *preference*, not a filter: the universe
        is unchanged and a placement whose only route is a FIELD still reports that route, which
        is what keeps `pathToWorld` answering for rows written before the gate existed and for
        the ones written straight through the ORM. Folding condensability into the walk's cost
        key instead would make a VALIDATED field route lose to an UNKNOWN affine one by a rule
        buried in a heap comparator; two passes say it where it can be read.
        """
        if source is None or self.world is None:
            return None
        container = self.container_of(source)
        affine = graph_logic._bfs_path(self.adjacency(container, at=at, require_affine=True), source.pk, self.world.pk)
        if affine is not None:
            return affine
        return graph_logic._bfs_path(self.adjacency(container, at=at), source.pk, self.world.pk)

    def condensed(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> "graph_logic.CondensedPlacement | None":
        """A source's whole path to world as one affine map, or None when there is no path.

        Built on :meth:`path`, not beside it, so `asAffine` condenses *exactly* the path
        `pathToWorld` reports -- same universe, same BFS, same tie-break. Two answers to
        "where is this" that could disagree would be worse than one the client has to
        compose itself.

        None exactly when the path is None, which is the same null `pathToWorld` returns and
        means the same two things; :meth:`state` is what tells them apart. A path that exists
        and does not condense is not a null -- it is an error, because there is something
        there and the honest answer is which edge stopped it.

        The source's axes are read off ``source.axes.all()``: a caller answering for many
        sources prefetches them, so this reads a cache rather than issuing a query each.
        """
        steps = self.path(source, at=at)
        if steps is None:
            return None

        return graph_logic.condense_path(
            steps,
            source_axes=[axis.name for axis in source.axes.all()],
            destination_axes=self.world_axes,
        )

    def representative_path(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The source's path, falling back to a route through its scoped edges when there is one.

        What the two aggregate questions below walk. They are asked *about the placement* --
        how well is it known, what survives it -- rather than about a point, and data
        corrected per channel has a placement whichever channel you mean. Walking only the
        unscoped adjacency answered UNKNOWN and NONE for it, which reads as "nothing is
        registered" for data that is registered several times over.

        The fallback is deliberately **not** offered by :meth:`path`, which answers "where is
        this" and must stay null until a coordinate is fixed: this route crosses an edge that
        holds at one index, so composing it without that index would state a per-channel
        correction as though it held everywhere. Nothing composes this one.

        With several scoped routes the walk returns one of them, so an aggregate over it is a
        reading of a representative route rather than of all of them. In the shape this exists
        for -- one correction per index of one axis, differing in their numbers rather than in
        their kind or their provenance -- every route gives the same answer; where they differ,
        ``at`` is the exact question and this is the summary.
        """
        direct = self.path(source, at=at)
        if direct is not None:
            return direct

        if source is None or self.world is None:
            return None
        container = self.container_of(source)
        # The same affine-first preference as `path`, for the same reason: the two aggregates
        # below summarise whichever route this returns, and summarising a warp field where an
        # affine route exists reads as DIFFEOMORPHIC for data that is rigidly placed.
        scoped_affine = graph_logic._bfs_path(
            self.adjacency(container, at=at, admit_scoped=True, require_affine=True),
            source.pk,
            self.world.pk,
        )
        if scoped_affine is not None:
            return scoped_affine
        return graph_logic._bfs_path(
            self.adjacency(container, at=at, admit_scoped=True),
            source.pk,
            self.world.pk,
        )

    def validity(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> str:
        """How much a source's placement is actually known: the weakest edge on its path.

        Derived, never stored -- validity is a fact about a *registration*, and the
        registration is an edge into the world. When it was a layer column, two layers over
        one dataset carried two copies of how-known one edge is, and nothing ever wrote
        either. Unplaced data is UNKNOWN (there is nothing to know the validity of); a source
        that already is the world has an exact placement.

        Data placed only per index reads the validity of one of its scoped routes -- see
        :meth:`representative_path` -- rather than UNKNOWN. Pass ``at`` for the exact answer.
        """
        steps = self.representative_path(source, at=at)
        if steps is None:
            return enums.PlacementValidityChoices.UNKNOWN.value
        # The empty path is VALIDATED, and that now falls out of the aggregate's default
        # rather than being restated here: a space's placement in itself is exact by
        # construction, which is a property of the order, not of layers.
        return graph_logic.weakest_validity(edge.validity for edge, _ in steps)

    def invariance(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> str:
        """Which geometric properties survive the whole walk from a source's data to world.

        The min-over-path twin of :meth:`validity`, and a minimum for a stronger reason than
        caution: the invariance groups nest, so a composition belongs to the weakest group
        any of its factors belongs to. An ``inverted`` step needs no handling -- every one of
        these classes is closed under inversion, the inverse of an isometry being an
        isometry, of a similarity a similarity.

        The same two edge cases as validity, at the same two ends of the order. Unplaced data
        is NONE: no path means nothing corresponds. A source that already IS the world is
        ISOMETRY, which falls out of :func:`~core.logic.graph.weakest_invariance` on no steps
        rather than being restated here -- a space is isometric to itself.

        NONE conflates "nobody has registered this yet" with "declared unmappable", exactly as
        UNKNOWN does for validity; :meth:`state` is the field that tells them apart.
        """
        steps = self.representative_path(source, at=at)
        if steps is None:
            return enums.TransformInvariance.NONE.value
        return graph_logic.weakest_invariance(graph_logic.invariance_of(edge) for edge, _ in steps)

    def state(self, source: "models.CoordinateSystem | None", *, at: dict[str, int] | None = None) -> str:
        """Whether a source has a place in the world, and if not, why not.

        ``pathToWorld`` being null means three very different things, and a client cannot
        tell them apart from the null alone: nobody has registered this data yet -- a gap,
        and authoring the edge closes it; its data reaches the world only across an
        UNMAPPABLE edge, in which case there is nothing to find and looking for the missing
        registration is a waste of a person's afternoon; or it is registered per index, and
        the question simply has not said which index.

        Derived from what the graph already holds, and stored nowhere: a second copy of
        this fact could disagree with the edges, and the edges would be right.
        """
        if self.path(source, at=at) is not None:
            return enums.PlacementState.PLACED.value

        container = self.container_of(source)

        # CONDITIONAL before the two gaps: a route exists, it just holds at coordinates this
        # question did not fix. Reporting UNREGISTERED here is what the per-index feature felt
        # like from a client's side -- data registered once per channel, badged as registered
        # nowhere -- and it is a placement, so it is answered before anything is called missing.
        if self.representative_path(source, at=at) is not None:
            return enums.PlacementState.CONDITIONAL.value

        if source is not None and container is not None:
            # **Does this data reach anywhere at all?** If a traversable edge takes it to any
            # other space, a registration authored from there would place it, and what is
            # missing is that registration. Asking only the second half below -- is any
            # lineage edge UNMAPPABLE -- badged a fusion with one unmappable parent as
            # impossible though registering its other parent places it, and sent whoever read
            # the badge away from a gap they could have closed. `graph_logic.reachable_in` is
            # the same traversal `assert_placeable_in` runs over its own universe, so
            # creation-time refusal and this answer cannot drift apart.
            if graph_logic.reachable_in(self.adjacency(container, admit_scoped=True), source.pk) != {source.pk}:
                return enums.PlacementState.UNREGISTERED.value

            # **Is there a stated non-correspondence to blame?** A collection's data (a feature
            # table) is unmappable when its derivation edge says so; a dataset's is when the
            # derivation it came out of does. One bucket answers for both -- a collection's
            # edges are its own bucket rather than a separate map keyed by system.
            if any(not graph_logic.is_traversable(edge) for edge in self.universe.container_edges.get(container, [])):
                return enums.PlacementState.UNMAPPABLE.value
            # An UNMAPPABLE registration -- a declared non-correspondence with the world
            # itself -- never enters a container bucket (no claim does), so it is read off
            # the world's own edges, scoped to this source's lineage: another container's
            # impossibility says nothing about this one.
            lineage = set(self.universe.lineage(container))
            if any(not graph_logic.is_traversable(edge) and edge_universe._edge_container(edge, self.keys) in lineage for edge in self.universe.root_edges):
                return enums.PlacementState.UNMAPPABLE.value

        return enums.PlacementState.UNREGISTERED.value
