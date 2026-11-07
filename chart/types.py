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
from core.render import filter_by as filter_by_models
from core.render.layer.types import LabelFilterBy
from core.types.coords import AffinePlacement, PlacementField, PlacementStep
from core.types._shared import DESCRIPTORS_DESCRIPTION, OrgScoped, build_prescoped_queryset, resolve_descriptors
from core.types.auth import User
from core.type_gen import create_stats_type
from kanne_server import scalars as kanne_scalars


@kante.django_type(
    models.Chart,
    filters=filters.ChartFilter,
    ordering=filters.ChartOrder,
    pagination=True,
    description=(
        "A composition that shows **how much**: values drawn as position. The counterpart of a scene, which shows **where**: in a scene every screen axis is a world axis, "
        "data is placed by the graph's edges and values are drawn as appearance (a colour, a size); in a chart at most one axis is drawn from the world and every other "
        "position is a value. Every chart has a world -- the domain its layers' rows live in -- and every layer is placed into it. An **AXIS** chart draws its world, one "
        "metric axis with a unit, and lays TRACE, SERIES and ANNOTATION layers along it: a timelapse's intensity against `t`. A **VALUES** chart does not draw its world, "
        "which says whose rows -- a table's space, usually its INDEX axis -- and its POINTS and DISTRIBUTION layers draw those rows at the values of the columns they name: "
        "nuclei at `area` against `volume`, a histogram of `area` per `condition`. Two layers in one VALUES world are about the same rows (a table and the PCA derived from "
        "it). The same table lens may be drawn as a scene, at its `(y, x)` coloured by `area`, and as a chart at `(area, volume)`. A chart names data through lenses "
        "and owns none of it; if rotating its view would mix units, it is a chart, and if it would not, a scene"
    ),
)
class Chart(OrgScoped, core_types.Visualization):
    """A composition that shows values as position."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, only=["stored_descriptors"], description=DESCRIPTORS_DESCRIPTION)

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
    layout: enums.ChartLayout = kante.django_field(description="How the layers share the screen, remembered with the chart: STACKED (one row or panel each), SHARED (overlaid where they read their axes alike) or OVERLAY (every along-axis layer in one plot)")
    world_coordinate_system: core_types.CoordinateSystem = kante.django_field(
        field_name="world",
        description=(
            "The domain this chart's layers live in: one measured axis its along-axis layers are laid along (`axis`), the space whose rows its value layers draw (a table's), "
            "or both at once (a table framed by `t`). Never owned by the chart: several charts can share it, it outlives each of them, and deleting a chart never deletes it. "
            "Ask it for `placedSystems` to learn what could be drawn here"
        ),
    )

    @kante.django_field(
        description=(
            "The one measured axis this chart's along-axis layers (TRACE, SERIES, ANNOTATION) are laid along: its name, its type and its unit -- the world's only axis, "
            "repeated so a client need not unwrap a list of one. Null when the world is not one measured axis, and then only value layers can be drawn here. A renderer "
            "reads it to know whether an axis panel exists before any layer does"
        )
    )
    def axis(self, info: Info) -> core_types.Axis | None:
        """The world's one measured axis, if it is that."""
        return chart_logic.world_axis(self.world)


@kante.django_interface(
    models.ChartLayer,
    description=(
        "A layer drawn in a chart. It carries view state only. A placed layer's position along the chart's axis -- `pathToWorld`, `asAffine`, `alongAxis`, `placement`, "
        "`placementValidity`, `placementInvariance` -- is derived from the graph on read and stored nowhere, so refining one registration moves every layer that looks "
        "through it. A value layer is placed the same way -- its rows into the chart's world, which says they are the same rows as the others' -- and drawn at the "
        "values of the columns it names. The concrete kind says what the layer reads: TraceChartLayer an array, SeriesChartLayer a table's value column, "
        "AnnotationChartLayer drawn marks -- along the chart's axis -- and PointsChartLayer and DistributionChartLayer a table's columns, by their values; `positioning` says which, and one chart may hold both"
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

    @kante.django_field(
        description=(
            "Where this layer's positions come from, read off its kind: ALONG_AXIS (TRACE, SERIES, ANNOTATION) -- laid along the chart's `axis` by the graph; BY_VALUES "
            "(POINTS, DISTRIBUTION) -- drawn at the values of the columns it names. What a renderer groups layers into panels by"
        )
    )
    def positioning(self, info: Info) -> enums.ChartLayerPositioning:
        """Along the world axis, or by its values."""
        return enums.ChartLayerPositioning(chart_logic.positioning_of(str(self.kind)))

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
        return AffinePlacement(
            matrix=condensed.matrix,
            input_axes=condensed.input_axes,
            output_axes=condensed.output_axes,
            total=condensed.total,
            fields=[PlacementField.from_form(axis, form, condensed.input_axes) for axis, form in condensed.fields.items()],
        )

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
    lens: core_types.ArrayLens = kante.django_field(disable_optimization=True, description="The array lens whose values are read")
    mark: enums.ChartMark = kante.django_field(description="How the values are drawn: a line, markers, both, or steps")
    line_width: float | None = kante.django_field(description="How thick lines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_size: float | None = kante.django_field(description="How big markers are, as a multiple of the chart's default: 1 is the default, 2 twice as big, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_shape: enums.ChartMarkerShape | None = kante.django_field(description="The symbol a marker is drawn as. Null lets the viewer choose (a circle)")

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
    lens: core_types.TableLens = kante.django_field(disable_optimization=True, description="The table lens this series reads through: the rows its windows keep")

    @kante.django_field(
        description="The lens' windows as row filters, always applied: a row is read when every one keeps it (`min <= column <= max`, an omitted side open). The selection the series draws, derived from the lens; empty for a whole lens"
    )
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' windows as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(description="The table dataset whose columns are read: the container of the lens")
    def table_dataset(self, info: Info) -> core_types.TableDataset:
        """The lens' table."""
        return self.lens.table_dataset
    value_column: str = kante.django_field(description="The numeric attribute column read as the value")
    mark: enums.ChartMark = kante.django_field(description="How the values are drawn: a line, markers, both, or steps")
    line_width: float | None = kante.django_field(description="How thick lines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_size: float | None = kante.django_field(description="How big markers are, as a multiple of the chart's default: 1 is the default, 2 twice as big, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_shape: enums.ChartMarkerShape | None = kante.django_field(description="The symbol a marker is drawn as. Null lets the viewer choose (a circle)")

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
        column = next((column for column in self.lens.table_dataset.columns.all() if column.name == self.value_column), None)
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
    lens: core_types.AnnotationLens = kante.django_field(disable_optimization=True, description="The annotation lens this layer draws through")

    @kante.django_field(description="The lens' windows as a clip box in the collection's own space: marks outside it are not drawn. Derived from the lens; empty for a whole lens")
    def clip(self, info: Info) -> List[core_types.Window]:
        """The lens' windows as a clip box."""
        del info
        return self.lens.windows_list

    @kante.django_field(description="The annotation collection whose marks this layer draws: the container of the lens. Its own coordinate system is the layer's space")
    def annotation_collection(self, info: Info) -> core_types.AnnotationCollection:
        """The lens' collection."""
        return self.lens.annotation_collection

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.ANNOTATION.value


_WINDOW_FILTERS = (
    "The lens' selection as row filters, always applied: a row is drawn when every one keeps it (`min <= column <= max`, an omitted side open). Derived from the lens; "
    "empty for a whole lens"
)


def _column_unit(lens: "core_types.TableLens", name: str | None) -> str | None:
    if name is None:
        return None
    column = next((column for column in lens.table_dataset.columns.all() if column.name == name), None)
    return column.unit if column is not None else None


@kante.django_type(
    models.ChartLayer,
    filters=filters.ChartLayerFilter,
    ordering=filters.ChartLayerOrder,
    pagination=True,
    description=(
        "A layer that draws a table lens' rows at the values of its columns: each row a point at (`xColumn`, `yColumn`), and at `zColumn` too for depth -- a scatter. "
        "The rows are placed in the chart's world -- whose rows -- and drawn at their values: the position is the measurement, read from the row. A row with a null "
        "in a named column is not drawn. "
        "MARKERS, or a LINE through the rows sorted along x. Example: nuclei at (`area`, `volume`), through a lens that keeps `area >= 50`"
    ),
)
class PointsChartLayer(ChartLayer):
    """A layer that draws a table lens' rows at the values of two or three columns."""

    id: auto
    lens: core_types.TableLens = kante.django_field(disable_optimization=True, description="The table lens whose rows are drawn: the rows its selection keeps")
    x_column: str = kante.django_field(description="The numeric column whose value is a row's horizontal position")
    y_column: str = kante.django_field(description="The numeric column whose value is a row's vertical position")
    z_column: str | None = kante.django_field(description="The numeric column whose value is a row's depth, for a three-dimensional scatter. Null for a flat one")
    mark: enums.ChartMark = kante.django_field(description="How the rows are drawn: MARKERS, or a LINE through them sorted along x")
    line_width: float | None = kante.django_field(description="How thick lines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_size: float | None = kante.django_field(description="How big markers are, as a multiple of the chart's default: 1 is the default, 2 twice as big, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")
    marker_shape: enums.ChartMarkerShape | None = kante.django_field(description="The symbol a marker is drawn as. Null lets the viewer choose (a circle)")

    @kante.django_field(description=_WINDOW_FILTERS)
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' selection as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(description="The table dataset whose columns are read: the container of the lens")
    def table_dataset(self, info: Info) -> core_types.TableDataset:
        """The lens' table."""
        return self.lens.table_dataset

    @kante.django_field(description="The unit of `xColumn`, as the table declares it: the chart's horizontal axis. Null when the column declares none")
    def x_unit(self, info: Info) -> kanne_scalars.Unit | None:
        return _column_unit(self.lens, self.x_column)

    @kante.django_field(description="The unit of `yColumn`, as the table declares it: the chart's vertical axis. Null when the column declares none")
    def y_unit(self, info: Info) -> kanne_scalars.Unit | None:
        return _column_unit(self.lens, self.y_column)

    @kante.django_field(description="The unit of `zColumn`, as the table declares it. Null for a flat scatter or a column that declares none")
    def z_unit(self, info: Info) -> kanne_scalars.Unit | None:
        return _column_unit(self.lens, self.z_column)

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.POINTS.value


@kante.django_type(
    models.ChartLayer,
    filters=filters.ChartLayerFilter,
    ordering=filters.ChartLayerOrder,
    pagination=True,
    description=(
        "A layer that summarises the values of one column of a table lens' rows: a HISTOGRAM (`bins`) or a BOX, over every row or once per value of `groupColumn` -- a "
        "condition, a cluster, a long-form channel. The rows are placed in the chart's world; their values are the horizontal axis. Example: the "
        "`area` of nuclei, one histogram per `condition`"
    ),
)
class DistributionChartLayer(ChartLayer):
    """A layer that summarises one column of a table lens' rows."""

    id: auto
    lens: core_types.TableLens = kante.django_field(disable_optimization=True, description="The table lens whose rows are summarised: the rows its selection keeps")
    value_column: str = kante.django_field(description="The numeric column whose values are summarised: the chart's horizontal axis")
    group_column: str | None = kante.django_field(description="The column whose values split the rows into one summary each: a text or boolean attribute, a label, a group id, or a long-form CHANNEL coordinate. Null for one summary over every row")
    bins: int | None = kante.django_field(description="How many bins a HISTOGRAM is cut into. Null lets the viewer choose")
    mark: enums.ChartMark = kante.django_field(description="How the values are summarised: a HISTOGRAM or a BOX")
    line_width: float | None = kante.django_field(description="How thick the outlines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half. Never pixels -- what the default is on screen is the viewer's. Null lets the viewer choose")

    @kante.django_field(description=_WINDOW_FILTERS)
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' selection as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(description="The table dataset whose columns are read: the container of the lens")
    def table_dataset(self, info: Info) -> core_types.TableDataset:
        """The lens' table."""
        return self.lens.table_dataset

    @kante.django_field(description="The unit of `valueColumn`, as the table declares it: the chart's horizontal axis. Null when the column declares none")
    def value_unit(self, info: Info) -> kanne_scalars.Unit | None:
        return _column_unit(self.lens, self.value_column)

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.ChartLayer) and obj.kind == enums.ChartLayerKind.DISTRIBUTION.value


#: The concrete layer types. Reachable only through the `ChartLayer` interface, so they must
#: be handed to the schema's ``types=`` list or strawberry drops them from the SDL.
chart_layer_types = [TraceChartLayer, SeriesChartLayer, AnnotationChartLayer, PointsChartLayer, DistributionChartLayer]

# The home page's count of charts, like the core kinds' in `core.types.stats`.
ChartStats, ChartStatsResolver = create_stats_type(
    models.Chart,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields={"created_at": "created_at"},
    filters=filters.ChartFilter,
)

__all__ = ["AnnotationChartLayer", "Chart", "ChartLayer", "ChartStats", "ChartStatsResolver", "DistributionChartLayer", "PointsChartLayer", "SeriesChartLayer", "TraceChartLayer", "chart_layer_types"]
