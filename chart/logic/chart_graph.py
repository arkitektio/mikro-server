"""One chart's placement questions, answered once per request.

The questions are :class:`core.logic.placement.PlacementGraph`'s, asked of a space. What a
chart adds is which space each of its layers draws from -- so the universe is fetched once
for the chart, however many layers ask.
"""

from kante.types import Info

from chart import models
from chart.logic import chart as chart_logic
from core.logic import placement

#: Where a `ChartGraph` memo lives on the request context, keyed by chart pk.
_LOADER_KEY = "chart_graphs"

#: The relations the placement logic reads off a layer in Python.
LAYER_PLACEMENT_RELATIONS = (
    "chart__world",
    "lens__dataset__coordinate_system",
    "lens__coordinate_system",
    "table_dataset__coordinate_system",
    "annotation_collection__coordinate_system",
)

#: The axes of every space a layer can name as its source: a reverse relation, so a prefetch.
LAYER_SOURCE_AXIS_PREFETCH = (
    "lens__coordinate_system__axes",
    "lens__dataset__coordinate_system__axes",
    "table_dataset__coordinate_system__axes",
    "annotation_collection__coordinate_system__axes",
)


class ChartGraph(placement.PlacementGraph):
    """The edges one chart's layers are placed by, fetched up front."""

    def __init__(self, chart: "models.Chart", *, loaders: dict | None = None) -> None:
        """Fetch the chart's layers, then the edge universe rooted at its world."""
        self.chart = chart
        self.layers = list(chart.layers.select_related(*LAYER_PLACEMENT_RELATIONS))

        systems = {source.pk for layer in self.layers if (source := chart_logic.layer_source_system(layer)) is not None}
        super().__init__(chart.world, seed_systems=systems, loaders=loaders)


def for_request(info: "Info", chart: "models.Chart") -> ChartGraph:
    """This chart's graph, built once per request (memoized as a scene's is)."""
    loaders = getattr(info.context, "_loaders", None)
    if loaders is None:
        return ChartGraph(chart)

    graphs = loaders.setdefault(_LOADER_KEY, {})
    if chart.pk not in graphs:
        graphs[chart.pk] = ChartGraph(chart, loaders=loaders)
    return graphs[chart.pk]
