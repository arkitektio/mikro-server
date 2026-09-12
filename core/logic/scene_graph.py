"""One scene's placement questions, answered over the edge universe of its world.

Where does this layer sit in world, where does each pyramid level sit -- every question a
*scene* can be asked is per layer, and every one of them is a search over the same edges:
the registrations into the scene's world, plus the scene-independent facts of the datasets
its layers draw from. That universe was being rebuilt from the database for every layer and
again for every field, which made a scene's cost quadratic in its layers for an answer that
does not change between them.

So it is built once, per scene, per request (see :func:`for_request`), in a fixed number of
queries no matter how many layers ask.

**The universe itself is not this module's.** It belongs to the world -- every scene over
one world searches exactly the same edges -- and lives in
:class:`core.logic.edge_universe.EdgeUniverse`, which :mod:`core.logic.space_graph` composes
too. What is left here is what is genuinely the scene's: its layer list, and the per-layer
answers. A question that reads no layer does not belong in this file; a question whose
answer is the same for two scenes over one world does not belong on a scene at all.
"""

from kante.types import Info

from core import enums, models
from core.logic import edge_universe
from core.logic import graph as graph_logic

#: Where a `SceneGraph` memo lives on the request context, keyed by scene pk.
_LOADER_KEY = "scene_graphs"



class SceneGraph:
    """The edges, layers and pyramid levels of one scene, fetched up front."""

    def __init__(self, scene: "models.Scene", *, loaders: dict | None = None) -> None:
        """Fetch the scene's layers, then the edge universe rooted at its world."""
        self.scene = scene

        self.world = scene.world

        # The layers, with every relation the placement logic walks in Python. The
        # optimizer cannot infer these: it prefetches what the *selection set* names, and
        # a client asking only for `pathToWorld` never names `lens`.
        self.layers = list(scene.layers.select_related(*LAYER_PLACEMENT_RELATIONS))

        # The scene's whole contribution to the universe: the spaces its layers draw from.
        # Seeding by *system* is right here and would be wrong for a space graph -- each
        # layer names one source space, so `residence_map` collapses nothing. Which of them
        # are collection-owned is a read off rows already in hand; asking each space what
        # lives in it was a query per layer.
        layer_systems: set[int] = set()
        collection_systems: set[int] = set()
        for layer in self.layers:
            source = graph_logic.layer_source_system(layer)
            if source is None:
                continue
            layer_systems.add(source.pk)
            if layer.mesh_collection_id or layer.table_dataset_id or layer.annotation_collection_id:
                collection_systems.add(source.pk)

        # No organization is passed, and that is deliberate: the world's edges are the
        # space's own truth, which is exactly what `CoordinateSystem.registrations` returns
        # unscoped. Scoping here would make `pathToWorld` search a narrower set than the
        # field that documents itself as its universe. `SpaceGraph` scopes because it hands
        # back whole containers; this one returns edges and systems. See `root_edges_of`.
        self.universe = edge_universe.EdgeUniverse(
            self.world,
            seed_systems=layer_systems | collection_systems,
            loaders=loaders,
        )

        self._levels: dict[int, list[models.DataArray]] | None = None
        self._world_axes: list[str] | None = None

    # --- the edge universe, which the space owns -----------------------------

    @property
    def keys(self) -> dict[int, tuple]:
        """``{space: container key}`` over every space this graph's edges touch."""
        return self.universe.keys

    def _container_of(self, system: "models.CoordinateSystem | None") -> tuple | None:
        """The container whose data lives in a space -- a dataset, or a collection itself."""
        return self.universe.container_of(system.pk) if system is not None else None

    def _layer_container(self, layer: "models.Layer") -> tuple | None:
        """The container a layer's source system belongs to, without touching the database."""
        return self._container_of(graph_logic.layer_source_system(layer))

    def adjacency(self, container_key: tuple | None, *, at: dict[str, int] | None = None, admit_scoped: bool = False, require_affine: bool = False) -> dict[int, list[tuple["models.Transformation", bool, int]]]:
        """The searchable edge universe for one container: its lineage's facts plus the world's claims."""
        return self.universe.adjacency(container_key, at=at, admit_scoped=admit_scoped, require_affine=require_affine)

    def _data_arrays(self, dataset_id: int) -> list["models.DataArray"]:
        """The pyramid levels of one dataset, from a single query covering every dataset in the scene."""
        if self._levels is None:
            self._levels = {dataset_id: [] for dataset_id in self.universe.dataset_ids}
            if self.universe.dataset_ids:
                arrays = models.DataArray.objects.filter(dataset__in=self.universe.dataset_ids).order_by("level").select_related("coordinate_system")
                for array in arrays:
                    self._levels.setdefault(array.dataset_id, []).append(array)
        return self._levels.get(dataset_id, [])

    # --- the questions -------------------------------------------------------

    def placement_path(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The path of edges from a layer's source system to this scene's world system.

        ``None`` when the layer has no source system or no path; ``[]`` when the source
        already is the world system.

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
        source = graph_logic.layer_source_system(layer)
        if source is None or self.world is None:
            return None
        container = self._layer_container(layer)
        affine = graph_logic._bfs_path(self.adjacency(container, at=at, require_affine=True), source.pk, self.world.pk)
        if affine is not None:
            return affine
        return graph_logic._bfs_path(self.adjacency(container, at=at), source.pk, self.world.pk)

    @property
    def world_axes(self) -> list[str]:
        """The world's axis order, read once per scene rather than once per layer.

        `self.world` is one object for the whole scene, so its axes are one fact -- but
        `axes.all()` is a query every time it is asked, and asking it inside a per-layer
        resolver is precisely the growth `test_scene_placements_are_flat_in_layer_count`
        exists to catch.
        """
        if self._world_axes is None:
            self._world_axes = [axis.name for axis in self.world.axes.all()] if self.world else []
        return self._world_axes

    def condensed_placement(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> "graph_logic.CondensedPlacement | None":
        """This layer's whole path to world as one affine map, or None when there is no path.

        Built on :meth:`placement_path`, not beside it, so `asAffine` condenses *exactly* the
        path `pathToWorld` reports -- same universe, same BFS, same tie-break. Two answers to
        "where is this layer" that could disagree would be worse than one the client has to
        compose itself.

        None exactly when the path is None, which is the same null `pathToWorld` returns and
        means the same two things; :meth:`placement_state` is what tells them apart. A path
        that exists and does not condense is not a null -- it is an error, because there is
        something there and the honest answer is which edge stopped it.
        """
        steps = self.placement_path(layer, at=at)
        if steps is None:
            return None

        source = graph_logic.layer_source_system(layer)
        if source is None or self.world is None:
            return None

        return graph_logic.condense_path(
            steps,
            # Prefetched with the layers (`LAYER_SOURCE_AXIS_PREFETCH`), so this reads a
            # cache rather than issuing a query per layer.
            source_axes=[axis.name for axis in source.axes.all()],
            destination_axes=self.world_axes,
        )

    def representative_path(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The layer's path, falling back to a route through its scoped edges when there is one.

        What the two aggregate questions below walk. They are asked *about the placement* --
        how well is it known, what survives it -- rather than about a point, and a layer
        corrected per channel has a placement whichever channel you mean. Walking only the
        unscoped adjacency answered UNKNOWN and NONE for it, which reads as "nothing is
        registered" for data that is registered several times over.

        The fallback is deliberately **not** offered by :meth:`placement_path`, which answers
        "where is this" and must stay null until a coordinate is fixed: this route crosses an
        edge that holds at one index, so composing it without that index would state a
        per-channel correction as though it held everywhere. Nothing composes this one.

        With several scoped routes the walk returns one of them, so an aggregate over it is a
        reading of a representative route rather than of all of them. In the shape this exists
        for -- one correction per index of one axis, differing in their numbers rather than in
        their kind or their provenance -- every route gives the same answer; where they differ,
        ``at`` is the exact question and this is the summary.
        """
        direct = self.placement_path(layer, at=at)
        if direct is not None:
            return direct

        source = graph_logic.layer_source_system(layer)
        if source is None or self.world is None:
            return None
        container = self._layer_container(layer)
        # The same affine-first preference as `placement_path`, for the same reason: the two
        # aggregates below summarise whichever route this returns, and summarising a warp field
        # where an affine route exists reads as DIFFEOMORPHIC for data that is rigidly placed.
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

    def placement_validity(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """How much this layer's placement is actually known: the weakest edge on its path.

        Derived, never stored -- validity is a fact about a *registration*, and the
        registration is a scene-level edge. When it was a layer column, two layers over
        one dataset carried two copies of how-known one edge is, and nothing ever wrote
        either. An unplaced layer is UNKNOWN (there is nothing to know the validity of);
        a layer whose source already is the world has an exact placement.

        A layer placed only per index reads the validity of one of its scoped routes -- see
        :meth:`representative_path` -- rather than UNKNOWN. Pass ``at`` for the exact answer.
        """
        steps = self.representative_path(layer, at=at)
        if steps is None:
            return enums.PlacementValidityChoices.UNKNOWN.value
        # The empty path is VALIDATED, and that now falls out of the aggregate's default
        # rather than being restated here: a space's placement in itself is exact by
        # construction, which is a property of the order, not of layers.
        return graph_logic.weakest_validity(edge.validity for edge, _ in steps)

    def placement_invariance(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """Which geometric properties survive the whole walk from this layer's data to world.

        The min-over-path twin of :meth:`placement_validity`, and a minimum for a stronger
        reason than caution: the invariance groups nest, so a composition belongs to the
        weakest group any of its factors belongs to. An ``inverted`` step needs no handling --
        every one of these classes is closed under inversion, the inverse of an isometry
        being an isometry, of a similarity a similarity.

        The same two edge cases as validity, at the same two ends of the order. An unplaced
        layer is NONE: no path means nothing corresponds. A layer whose source already IS the
        world is ISOMETRY, which falls out of :func:`~core.logic.graph.weakest_invariance` on
        no steps rather than being restated here -- a space is isometric to itself.

        NONE conflates "nobody has registered this yet" with "declared unmappable", exactly as
        UNKNOWN does for validity; :meth:`placement_state` is the field that tells them apart.
        """
        steps = self.representative_path(layer, at=at)
        if steps is None:
            return enums.TransformInvariance.NONE.value
        return graph_logic.weakest_invariance(graph_logic.invariance_of(edge) for edge, _ in steps)

    def placement_state(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """Whether this layer has a place in the world, and if not, why not.

        ``pathToWorld`` being null means three very different things, and a client cannot
        tell them apart from the null alone: nobody has registered this data yet -- a gap,
        and authoring the edge closes it; its data reaches the world only across an
        UNMAPPABLE edge, in which case there is nothing to find and looking for the missing
        registration is a waste of a person's afternoon; or it is registered per index, and
        the question simply has not said which index.

        Derived from what the graph already holds, and stored nowhere: a second copy of
        this fact could disagree with the edges, and the edges would be right.
        """
        if self.placement_path(layer, at=at) is not None:
            return enums.PlacementState.PLACED.value

        source = graph_logic.layer_source_system(layer)
        container = self._layer_container(layer)

        # CONDITIONAL before the two gaps: a route exists, it just holds at coordinates this
        # question did not fix. Reporting UNREGISTERED here is what the per-index feature felt
        # like from a client's side -- data registered once per channel, badged as registered
        # nowhere -- and it is a placement, so it is answered before anything is called missing.
        if self.representative_path(layer, at=at) is not None:
            return enums.PlacementState.CONDITIONAL.value

        if source is not None and container is not None:
            # **Does this layer's data reach anywhere at all?** If a traversable edge takes it
            # to any other space, a registration authored from there would place it, and what
            # is missing is that registration. Asking only the second half below -- is any
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
            # the world's own edges, scoped to this layer's lineage: another container's
            # impossibility says nothing about this one.
            lineage = set(self.universe.lineage(container))
            if any(not graph_logic.is_traversable(edge) and edge_universe._edge_container(edge, self.keys) in lineage for edge in self.universe.root_edges):
                return enums.PlacementState.UNMAPPABLE.value

        return enums.PlacementState.UNREGISTERED.value

    def level_placements(self, layer: "models.Layer") -> list[tuple["models.DataArray", list[tuple["models.Transformation", bool]] | None]]:
        """Per pyramid level, the path from that level's voxel grid to this scene's world system.

        Every lens-backed kind, and the set is read rather than spelled: a pyramid is a fact
        about the array, so a label map, an intensity channel, an RGB photograph and a phasor
        cube all have one, and a multiscale renderer picks a level off each exactly as it does
        for a general image layer. How the level is *drawn* is the only thing that differs,
        and nothing here asks.
        """
        if layer.kind not in enums.LENS_BACKED_KINDS or not layer.lens_id:
            return []

        dataset_id = layer.lens.dataset_id
        arrays = self._data_arrays(dataset_id)
        if self.world is None:
            return [(array, None) for array in arrays]

        # Both adjacencies, built once for every level rather than once per level: they are
        # memoized on the universe, so the second pass costs a dict lookup.
        affine_adjacency = self.adjacency(("dataset", dataset_id), require_affine=True)
        adjacency = self.adjacency(("dataset", dataset_id))
        # Level 0 owns no system -- its voxel space IS the dataset's intrinsic system, which
        # rides along on the layer's prefetched lens, so the fallback costs no query.
        intrinsic = layer.lens.dataset.intrinsic_coordinate_system
        placements = []
        for array in arrays:
            system = getattr(array, "coordinate_system", None) or (intrinsic if array.level == 0 else None)
            if system is None:
                placements.append((array, None))
                continue
            # Affine-first, exactly as `placement_path`: a level reporting a warp route while
            # the layer over it reports an affine one would be two answers to one question.
            path = graph_logic._bfs_path(affine_adjacency, system.pk, self.world.pk)
            if path is None:
                path = graph_logic._bfs_path(adjacency, system.pk, self.world.pk)
            placements.append((array, path))
        return placements

    # `reachable_system_ids` / `reachable_systems` used to live here, answering
    # `Scene.coordinateSystems` and `Scene.annotations`. Both are gone: the question is
    # "what can reach this space", the answer is the same for every scene over one world,
    # and `graph_logic.placeable_system_ids_in` already answered it correctly -- where this
    # closure ran over `_world_edges` alone and so both under- and over-reported. The
    # fields now hang off `CoordinateSystem` as `placedSystems` and `annotations`.


#: The axes of every space a layer can name as its source, one prefetch each. Separate from
#: `LAYER_PLACEMENT_RELATIONS` because axes are a *reverse* relation: `select_related` cannot
#: follow one, and `asAffine` needs the source's axis order to label its matrix's columns.
#:
#: **Applied by `Layer.get_queryset`, not here.** This class fetches its own layer rows to
#: seed the edge universe, but the layer a resolver hands to `condensed_placement` is the
#: resolver's own instance -- a prefetch on `self.layers` populates a different object and
#: buys nothing. Five queries for a whole scene, against one per layer without it.
LAYER_SOURCE_AXIS_PREFETCH = (
    "lens__coordinate_system__axes",
    "lens__dataset__coordinate_system__axes",
    "annotation_collection__coordinate_system__axes",
    "mesh_collection__coordinate_system__axes",
    "table_dataset__coordinate_system__axes",
)

#: The relations the placement logic reads off a layer in Python. `Layer` is one table
#: discriminated by `kind`, so a single select_related covers every layer kind.
LAYER_PLACEMENT_RELATIONS = (
    "scene__world",
    "lens__dataset__coordinate_system",
    "lens__coordinate_system",
    "annotation_collection__coordinate_system",
    "mesh_collection__coordinate_system",
    "table_dataset__coordinate_system",
)


def for_request(info: "Info", scene: "models.Scene") -> SceneGraph:
    """This scene's graph, built once per request.

    Memoized on the context's ``_loaders`` -- the per-request store kante already carries
    for exactly this (``kante.context.HttpContext``). Without it, every layer of a scene
    would rebuild the scene's whole edge universe to ask its one question about it.

    ``loaders`` is handed on to the universe, which memoizes the world's edges under its own
    key: two scenes over one world in a single request then share that fetch, because those
    edges are the world's and not either scene's.
    """
    context = info.context
    loaders = getattr(context, "_loaders", None)
    if loaders is None:
        return SceneGraph(scene)

    graphs = loaders.setdefault(_LOADER_KEY, {})
    if scene.pk not in graphs:
        graphs[scene.pk] = SceneGraph(scene, loaders=loaders)
    return graphs[scene.pk]
