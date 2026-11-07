"""One grid's placement questions, answered once per request -- the chart's pattern, over a grid's world."""

from django.db.models import Prefetch
from kante.types import Info

from core import models as core_models
from core.logic import placement
from grid import models
from grid.logic import grid as grid_logic

#: Where a `GridGraph` memo lives on the request context, keyed by grid pk.
_LOADER_KEY = "grid_graphs"

#: The relations the placement logic reads off a layer in Python. The lens arrives through the
#: prefetch below: a select_related through a polymorphic FK hands back the base row.
LAYER_PLACEMENT_RELATIONS = ("grid__world",)

#: The axes of every space a layer can name as its source.
LAYER_SOURCE_AXIS_PREFETCH = (Prefetch("lens", queryset=core_models.Lens.objects.with_sources()),)


class GridGraph(placement.PlacementGraph):
    """The edges one grid's layers are placed by, fetched up front."""

    def __init__(self, grid: "models.Grid", *, loaders: dict | None = None) -> None:
        self.grid = grid
        self.layers = list(grid.layers.select_related(*LAYER_PLACEMENT_RELATIONS).prefetch_related(*LAYER_SOURCE_AXIS_PREFETCH))
        systems = {source.pk for layer in self.layers if (source := grid_logic.layer_source_system(layer)) is not None}
        super().__init__(grid.world, seed_systems=systems, loaders=loaders)


def for_request(info: "Info", grid: "models.Grid") -> GridGraph:
    """This grid's graph, built once per request."""
    loaders = getattr(info.context, "_loaders", None)
    if loaders is None:
        return GridGraph(grid)
    graphs = loaders.setdefault(_LOADER_KEY, {})
    if grid.pk not in graphs:
        graphs[grid.pk] = GridGraph(grid, loaders=loaders)
    return graphs[grid.pk]
