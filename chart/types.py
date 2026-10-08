"""The chart's GraphQL types. Every spatial answer here is derived from the graph on read."""

import datetime
from typing import List

import kante
from kante.types import Info
from strawberry import auto
from strawberry.scalars import JSON

from chart import enums, filters, models
from chart.logic import chart as chart_logic
from chart.logic import chart_graph
from core import enums as core_enums
from core import types as core_types
from core.inputs.coords import CoordinateInput, at_map
from core.types.coords import AffinePlacement, PlacementStep
from core.types._shared import DESCRIPTORS_DESCRIPTION, OrgScoped, build_prescoped_queryset, resolve_descriptors
from core.types.auth import User
from kanne_server import scalars as kanne_scalars


@kante.django_type(
    models.Chart,
    filters=filters.ChartFilter,
    ordering=filters.ChartOrder,
    pagination=True,
    description=(
        "A composition of data laid out along one metric axis, with values read off it. The second kind of composition beside a scene: a scene is a place, a chart is an "
        "axis. It names data by id and owns none of it, and it carries no unit of its own -- the unit is its world axis's"
    ),
)
class Chart(OrgScoped):
    """A composition of layers along one metric axis."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, description=DESCRIPTORS_DESCRIPTION)

    id: auto
    name: auto
    description: str | None
    created_at: datetime.datetime
    creator: User | None
    layers: List["ChartLayer"] = kante.django_field(
        filters=filters.ChartLayerFilter,
        ordering=filters.ChartLayerOrder,
        pagination=True,
        description="The layers drawn in this chart, back to front (a heterogeneous list of layer kinds)",
    )
    world_coordinate_system: core_types.CoordinateSystem = kante.django_field(
        field_name="world",
        description=(
            "The space this chart is laid out along. Never owned by the chart: several charts can share it, it outlives each of them, and deleting a chart never deletes it. "
            "Ask it for `placedSystems` to learn what could be drawn here"
        ),
    )

    @kante.django_field(description="The one axis this chart is laid out along: its name, its metric type and its unit. The world's only axis, repeated here so a client drawing the chart need not unwrap a list of one")
    def axis(self, info: Info) -> core_types.Axis:
        """The world's one axis."""
        return chart_logic.chart_axis(self.world)


@kante.django_interface(
    models.ChartLayer,
    description=(
        "A layer drawn in a chart. It carries view state only. Where its data sits along the chart's axis -- `pathToWorld`, `asAffine`, `alongAxis`, `placement`, "
        "`placementValidity`, `placementInvariance` -- is derived from the graph on read and stored nowhere, so refining one registration moves every layer that looks "
        "through it. The concrete kind says what the layer reads: TraceChartLayer an array, SeriesChartLayer a table, AnnotationChartLayer drawn marks"
    ),
)
class ChartLayer:
    """A layer drawn in a chart, carrying the shared view state. No spatial fields."""

    id: auto
    kind: enums.ChartLayerKind
    name: str | None
    chart: Chart
    visible: bool
    order: int
    opacity: float
    color: list[float] | None = kante.django_field(description="The colour the layer is drawn in, as RGBA. Null lets the viewer choose")

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):  # noqa: ANN001, ANN003, ANN206 - strawberry_django's hook
        """Scope to the request's organization, and select what the placement logic reads in Python."""
        queryset = build_prescoped_queryset(info, queryset)
        return queryset.select_related(*chart_graph.LAYER_PLACEMENT_RELATIONS).prefetch_related(*chart_graph.LAYER_SOURCE_AXIS_PREFETCH)

    @kante.django_field(
        description=(
            "The path of transformation edges from this layer's source coordinate system to its chart's world. Null when the layer is unregistered; empty when the source "
            "already is the world. `asAffine` is the same path composed"
        ),
    )
    def path_to_world(self, info: Info, at: List[CoordinateInput] | None = None) -> List[PlacementStep] | None:
        """The layer's placement path, as (edge, inverted) steps."""
        steps = chart_graph.for_request(info, self.chart).path(chart_logic.layer_source_system(self), at=at_map(at))
        if steps is None:
            return None
        return [PlacementStep(transformation=edge, inverted=inverted) for edge, inverted in steps]

    @kante.django_field(
        description=(
            "This layer's whole `pathToWorld` composed into one affine map: a single row, because the chart's world has a single axis. The coefficient on `alongAxis` is "
            "the scale from a step along the data to a step along the chart's axis, in the axis's unit, and the last entry is the offset. Derived on read. Null when "
            "`pathToWorld` is null; an error when a path exists but does not condense"
        ),
    )
    def as_affine(self, info: Info, at: List[CoordinateInput] | None = None) -> AffinePlacement | None:
        """The layer's placement path composed into one labelled affine map."""
        condensed = chart_graph.for_request(info, self.chart).condensed(chart_logic.layer_source_system(self), at=at_map(at))
        if condensed is None:
            return None
        return AffinePlacement(matrix=condensed.matrix, input_axes=condensed.input_axes, output_axes=condensed.output_axes, total=condensed.total)

    @kante.django_field(
        description=(
            "The name of the source axis that runs along the chart's axis: for a trace, the axis of its lens the values are read along; for a series, the table's coordinate "
            "column; for an annotation layer, the axis of the drawing space that is the chart's own. **Not a setting.** It is the one source axis the composed placement reads "
            "the chart's axis from, so it follows the registration: re-register the data by another axis and this changes with no write to the layer. Null when the layer is "
            "no longer placed, or is placed by a map that reads the chart's axis from several source axes"
        ),
    )
    def along_axis(self, info: Info, at: List[CoordinateInput] | None = None) -> str | None:
        """The source axis the placement lays along the chart."""
        graph = chart_graph.for_request(info, self.chart)
        source = chart_logic.layer_source_system(self)
        if graph.path(source, at=at_map(at)) is None:
            return None
        try:
            return chart_logic.along_axis_of(graph.condensed(source, at=at_map(at)))
        except ValueError:
            # A route that exists and does not condense: `asAffine` is where that is an error,
            # naming the edge. Here it is one more way of having no axis to name.
            return None

    @kante.django_field(description="Whether this layer has a place along its chart's axis, and if not, why not. UNREGISTERED is a gap to close; UNMAPPABLE is a fact to badge; CONDITIONAL is a placement to ask again for with `at`. Derived, never stored")
    def placement(self, info: Info, at: List[CoordinateInput] | None = None) -> core_enums.PlacementState:
        """PLACED, CONDITIONAL, UNREGISTERED or UNMAPPABLE."""
        return core_enums.PlacementState(chart_graph.for_request(info, self.chart).state(chart_logic.layer_source_system(self), at=at_map(at)))

    @kante.django_field(description="How much this layer's placement is actually known: the weakest edge on its path to the chart's world. Derived, never stored")
    def placement_validity(self, info: Info, at: List[CoordinateInput] | None = None) -> core_enums.PlacementValidity:
        """The weakest validity on the layer's placement path."""
        return core_enums.PlacementValidity(chart_graph.for_request(info, self.chart).validity(chart_logic.layer_source_system(self), at=at_map(at)))

    @kante.django_field(description="Which geometric properties survive the walk from this layer's data to the chart's world: the weakest edge on its path. Derived, never stored")
    def placement_invariance(self, info: Info, at: List[CoordinateInput] | None = None) -> core_enums.TransformInvariance:
        """The weakest invariance class on the layer's placement path."""
        return core_enums.TransformInvariance(chart_graph.for_request(info, self.chart).invariance(chart_logic.layer_source_system(self), at=at_map(at)))


@kante.django_type(
    models.ChartLayer,
    filters=filters.ChartLayerFilter,
    ordering=filters.ChartLayerOrder,
    pagination=True,
    description=(
        "A layer that reads an array along one axis. Its lens leaves one metric axis free -- `alongAxis`, laid along the chart -- and optionally one enumerating axis, "
        "`seriesAxis`, drawn as one line per position. Whether it is drawn as a line, markers, both or steps is `mark`"
    ),
)
class TraceChartLayer(ChartLayer):
    """A layer that reads a lens along one axis."""

    id: auto
    lens: core_types.Lens = kante.django_field(description="The lens whose values are read")
    mark: enums.ChartMark = kante.django_field(description="How the values are drawn: a line, markers, both, or steps")
    line_width: float | None = kante.django_field(description="Line width in screen pixels. Null lets the viewer choose")
    marker_size: float | None = kante.django_field(description="Marker size in screen pixels. Null lets the viewer choose")

    @kante.django_field(description="The CHANNEL or INDEX axis the lens leaves free beside `alongAxis`, drawn as one line per position along it. Null when the lens is a single line. Read from the lens' shape, not stored")
    def series_axis(self, info: Info) -> str | None:
        """The enumerating axis left free beside the one laid along the chart."""
        graph = chart_graph.for_request(info, self.chart)
        source = chart_logic.layer_source_system(self)
        if graph.path(source) is None:
            return None
        try:
            along = chart_logic.along_axis_of(graph.condensed(source))
        except ValueError:
            return None
        return chart_logic.series_axis_of(self.lens, along) if along is not None else None

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.TRACE.value


@kante.django_type(
    models.ChartLayer,
    filters=filters.ChartLayerFilter,
    ordering=filters.ChartLayerOrder,
    pagination=True,
    description=(
        "A layer that reads one numeric column of a table against a coordinate column. The value column is the layer's; the coordinate column is the graph's -- whichever "
        "of the table's coordinate columns its registration lays along the chart's axis"
    ),
)
class SeriesChartLayer(ChartLayer):
    """A layer that reads a table's value column against a coordinate column."""

    id: auto
    table_dataset: core_types.TableDataset = kante.django_field(description="The table dataset whose columns are read")
    value_column: str = kante.django_field(description="The numeric attribute column read as the value")
    mark: enums.ChartMark = kante.django_field(description="How the values are drawn: a line, markers, both, or steps")
    line_width: float | None = kante.django_field(description="Line width in screen pixels. Null lets the viewer choose")
    marker_size: float | None = kante.django_field(description="Marker size in screen pixels. Null lets the viewer choose")

    @kante.django_field(description="The coordinate column the values are read against: the same name as `alongAxis`, since a table's coordinate columns are its space's axes. Null when the table is no longer placed")
    def coordinate_column(self, info: Info) -> str | None:
        """The table column laid along the chart."""
        graph = chart_graph.for_request(info, self.chart)
        source = chart_logic.layer_source_system(self)
        if graph.path(source) is None:
            return None
        try:
            return chart_logic.along_axis_of(graph.condensed(source))
        except ValueError:
            return None

    @kante.django_field(description="The unit the value column's values are in, as the table declares it. Null when the column declares none. A chart may hold layers in several units: it commits only to its axis")
    def value_unit(self, info: Info) -> kanne_scalars.Unit | None:
        """The declared unit of the value column."""
        column = next((column for column in self.table_dataset.columns.all() if column.name == self.value_column), None)
        return column.unit if column is not None else None

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.SERIES.value


@kante.django_type(
    models.ChartLayer,
    filters=filters.ChartLayerFilter,
    ordering=filters.ChartLayerOrder,
    pagination=True,
    description="A layer that draws an annotation collection's marks in a chart. One layer per collection: per-mark styling lives on the annotations themselves",
)
class AnnotationChartLayer(ChartLayer):
    """A layer that draws an annotation collection's marks in a chart."""

    id: auto
    annotation_collection: core_types.AnnotationCollection = kante.django_field(description="The annotation collection whose marks this layer draws. Its own coordinate system is the layer's space")

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.ANNOTATION.value


#: The concrete layer types. Reachable only through the `ChartLayer` interface, so they must
#: be handed to the schema's ``types=`` list or strawberry drops them from the SDL.
chart_layer_types = [TraceChartLayer, SeriesChartLayer, AnnotationChartLayer]

__all__ = ["AnnotationChartLayer", "Chart", "ChartLayer", "SeriesChartLayer", "TraceChartLayer", "chart_layer_types"]
