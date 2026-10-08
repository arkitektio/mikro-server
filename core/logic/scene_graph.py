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
from core.logic import placement
from core.logic import graph as graph_logic

#: Where a `SceneGraph` memo lives on the request context, keyed by scene pk.
_LOADER_KEY = "scene_graphs"



class SceneGraph(placement.PlacementGraph):
    """The edges, layers and pyramid levels of one scene, fetched up front.

    The placement questions themselves are :class:`core.logic.placement.PlacementGraph`'s,
    asked of a source space; what this adds is the scene's own half -- which space each of
    its layers draws from, and the pyramid levels behind them.
    """

    def __init__(self, scene: "models.Scene", *, loaders: dict | None = None) -> None:
        """Fetch the scene's layers, then the edge universe rooted at its world."""
        self.scene = scene

        # The layers, with every relation the placement logic walks in Python. The
        # optimizer cannot infer these: it prefetches what the *selection set* names, and
        # a client asking only for `pathToWorld` never names `lens`.
        self.layers = list(scene.layers.select_related(*LAYER_PLACEMENT_RELATIONS))

        # The scene's whole contribution to the universe: the spaces its layers draw from.
        # Seeding by *system* is right here and would be wrong for a space graph -- each
        # layer names one source space, so `residence_map` collapses nothing.
        layer_systems: set[int] = set()
        for layer in self.layers:
            source = graph_logic.layer_source_system(layer)
            if source is not None:
                layer_systems.add(source.pk)

        super().__init__(scene.world, seed_systems=layer_systems, loaders=loaders)

        self._levels: dict[int, list[models.DataArray]] | None = None

    def _data_arrays(self, dataset_id: int) -> list["models.DataArray"]:
        """The pyramid levels of one dataset, from a single query covering every dataset in the scene."""
        if self._levels is None:
            self._levels = {dataset_id: [] for dataset_id in self.universe.dataset_ids}
            if self.universe.dataset_ids:
                arrays = models.DataArray.objects.filter(dataset__in=self.universe.dataset_ids).order_by("level").select_related("coordinate_system")
                for array in arrays:
                    self._levels.setdefault(array.dataset_id, []).append(array)
        return self._levels.get(dataset_id, [])

    # --- the questions, per layer --------------------------------------------
    #
    # Each is the same question `PlacementGraph` answers for a space, asked of the space this
    # layer draws from. The reasoning is documented there, once.

    def placement_path(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> list[tuple["models.Transformation", bool]] | None:
        """The path of edges from a layer's source system to this scene's world system. See :meth:`core.logic.placement.PlacementGraph.path`."""
        return self.path(graph_logic.layer_source_system(layer), at=at)

    def condensed_placement(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> "graph_logic.CondensedPlacement | None":
        """This layer's whole path to world as one affine map, or None when there is no path.

        The source's axes are prefetched with the layers (`LAYER_SOURCE_AXIS_PREFETCH`), so
        this reads a cache rather than issuing a query per layer.
        """
        return self.condensed(graph_logic.layer_source_system(layer), at=at)

    def placement_validity(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """How much this layer's placement is actually known: the weakest edge on its path."""
        return self.validity(graph_logic.layer_source_system(layer), at=at)

    def placement_invariance(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """Which geometric properties survive the whole walk from this layer's data to world."""
        return self.invariance(graph_logic.layer_source_system(layer), at=at)

    def placement_state(self, layer: "models.Layer", *, at: dict[str, int] | None = None) -> str:
        """Whether this layer has a place in the world, and if not, why not."""
        return self.state(graph_logic.layer_source_system(layer), at=at)

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
