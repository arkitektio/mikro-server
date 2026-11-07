"""Chart layers: one creator per kind, because each kind reads a different source.

A placed kind (trace, series, annotation) runs the same gate before it writes -- the source
must be laid along the chart's axis by the graph. A value kind (points, distribution) names
the columns whose values are its positions, in a VALUES chart whose world its rows are placed in. None of them writes an edge.
"""

import kante
import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, Field, field_validator

from chart import enums, models, types
from chart.logic import chart as chart_logic
from core import models as core_models
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.logic import coordinate_system as coordinate_system_logic
from core.inputs.validators import Alpha, assert_rgba
from core.mutations._generic import make_delete
from core.mutations._sources import lens_of_kind
from core.scoping import get_for_org


class _Style(BaseModel):
    """The view state every creator and the one update take. Omitted means the default, or 'leave it'."""

    name: str | None = None
    visible: bool | None = None
    order: int | None = None
    opacity: Alpha | None = None
    color: list[float] | None = None
    mark: enums.ChartMark | None = None
    line_width: float | None = Field(default=None, gt=0)
    marker_size: float | None = Field(default=None, gt=0)
    marker_shape: enums.ChartMarkerShape | None = None

    @field_validator("color")
    @classmethod
    def _is_rgba(cls, color: list[float] | None) -> list[float] | None:
        if color is not None:
            assert_rgba(color, field="color")
        return color


def _settings(model: _Style) -> dict[str, object]:
    """The style a caller actually named, as column values."""
    named = {field: getattr(model, field) for field in ("name", "visible", "order", "opacity", "color") if getattr(model, field) is not None}
    for field in ("line_width", "marker_size"):
        if getattr(model, field) is not None:
            named[field] = getattr(model, field)
    for field in ("mark", "marker_shape"):
        value = getattr(model, field)
        if value is not None:
            named[field] = value.value
    return named


_NAME = "A human-readable name for the layer"
_VISIBLE = "Whether the layer is drawn. Defaults to true"
_ORDER = "Explicit drawing order, back to front. Defaults to after the chart's last layer"
_OPACITY = "Layer alpha, from 0 (transparent) to 1 (opaque)"
_COLOR = "The colour the layer is drawn in, as RGBA: exactly four components. Omit to let the viewer choose"
_MARK = "How the values are drawn: a line, markers, both, or steps. A look, not a kind. Defaults to LINE"
_LINE_WIDTH = "How thick lines are, as a multiple of the chart's default: 1 is the default, 2 twice as thick, 0.5 half -- never pixels. Omit to let the viewer choose"
_MARKER_SIZE = "How big markers are, as a multiple of the chart's default: 1 is the default, 2 twice as big, 0.5 half -- never pixels. Omit to let the viewer choose"
_MARKER_SHAPE = "The symbol a marker is drawn as: CIRCLE, SQUARE, TRIANGLE, DIAMOND, CROSS or PLUS. Omit to let the viewer choose (a circle)"


# --- trace --------------------------------------------------------------------


class CreateTraceChartLayerInputModel(_Style):
    chart: str
    lens: str


@prose_errors
@kante.pydantic_input(
    CreateTraceChartLayerInputModel,
    description=(
        "Input for drawing an array as a trace in a chart. The lens must leave one metric axis free -- the one the graph lays along the chart's axis -- and may leave one "
        "CHANNEL or INDEX axis free beside it, drawn as one line per position; every other axis must be sliced to a single position. The lens' data must already be "
        "registered into the chart's world: this writes no edge"
    ),
)
class CreateTraceChartLayerInput:
    """Input for creating a trace layer."""

    chart: strawberry.ID = strawberry.field(description="The chart to draw in")
    lens: strawberry.ID = strawberry.field(description="The array lens to read as a trace. A dataset's whole lens is its `fullLens`")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description=_MARK)
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def create_trace_chart_layer(info: Info, input: CreateTraceChartLayerInput) -> types.TraceChartLayer:
    """Draw a lens as a trace along a chart's axis."""
    model = input.to_pydantic()
    chart = get_for_org(models.Chart, info, id=model.chart)

    with transaction.atomic():
        lens = lens_of_kind(info, kind=core_models.ArrayLens, lens=model.lens, role="a trace reads")
        return chart_logic.create_layer(chart, kind=enums.ChartLayerKindChoices.TRACE.value, source=lens, **_settings(model))


# --- series -------------------------------------------------------------------


class CreateSeriesChartLayerInputModel(_Style):
    chart: str
    lens: str
    value_column: str | None = None


@prose_errors
@kante.pydantic_input(
    CreateSeriesChartLayerInputModel,
    description=(
        "Input for drawing a table as a series in a chart: one numeric column read as the value against the coordinate column the graph lays along the chart's axis. The "
        "coordinate column is not named here -- it is whichever of the table's coordinate columns its registration maps onto the chart's axis. The table must already be "
        "registered into the chart's world: this writes no edge"
    ),
)
class CreateSeriesChartLayerInput:
    """Input for creating a series layer."""

    chart: strawberry.ID = strawberry.field(description="The chart to draw in")
    lens: strawberry.ID = strawberry.field(description="The table lens to read as a series: which rows. A table's whole lens is its `fullLens`")
    value_column: str | None = strawberry.field(default=None, description="The numeric attribute column read as the value. May be omitted when the table has exactly one")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description=_MARK)
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def create_series_chart_layer(info: Info, input: CreateSeriesChartLayerInput) -> types.SeriesChartLayer:
    """Draw a table's value column against the coordinate column laid along a chart's axis."""
    model = input.to_pydantic()
    chart = get_for_org(models.Chart, info, id=model.chart)
    lens = lens_of_kind(info, kind=core_models.TableLens, lens=model.lens, role="a series reads")

    with transaction.atomic():
        return chart_logic.create_layer(chart, kind=enums.ChartLayerKindChoices.SERIES.value, source=lens, value_column=model.value_column, **_settings(model))


# --- annotation ---------------------------------------------------------------


class CreateAnnotationChartLayerInputModel(BaseModel):
    chart: str
    lens: str | None = None
    name: str | None = None
    visible: bool | None = None
    order: int | None = None
    opacity: Alpha | None = None


@prose_errors
@kante.pydantic_input(
    CreateAnnotationChartLayerInputModel,
    description=(
        "Input for drawing an annotation collection's marks in a chart. Name an existing collection that is registered into the chart's world, or omit it to have a new "
        "drawing surface made for the chart: a collection whose space has the chart's axis and a VALUE axis, registered into the chart's world along the chart's axis. "
        "Marks are then drawn into it with `createAnnotation(collection:)`, at a position along the axis and a height along `value`"
    ),
)
class CreateAnnotationChartLayerInput:
    """Input for creating an annotation layer."""

    chart: strawberry.ID = strawberry.field(description="The chart to draw in")
    lens: strawberry.ID | None = strawberry.field(default=None, description="The annotation lens to draw through -- a collection's whole lens is its `fullLens`. Omit to mint a fresh collection in the chart's world and draw its whole lens")
    name: str | None = strawberry.field(default=None, description="A human-readable name for the layer, and for the collection when one is made")
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)


def create_annotation_chart_layer(info: Info, input: CreateAnnotationChartLayerInput) -> types.AnnotationChartLayer:
    """Draw an annotation collection in a chart, making the collection when none is named."""
    model = input.to_pydantic()
    ctx = CreationContext.from_info(info)
    chart = get_for_org(models.Chart, info, id=model.chart)

    settings = {field: getattr(model, field) for field in ("name", "visible", "order", "opacity") if getattr(model, field) is not None}
    with transaction.atomic():
        if model.lens is not None:
            lens = lens_of_kind(info, kind=core_models.AnnotationLens, lens=model.lens, role="an annotation chart layer draws through")
            if chart.layers.filter(lens=lens).exists():
                raise ValueError(f"Lens {lens.pk} over annotation collection {lens.annotation_collection_id} is already drawn in chart '{chart.name}'. One layer per lens: per-mark styling lives on the annotations, and another window over the collection is another lens.")
        else:
            lens = coordinate_system_logic.whole_lens(chart_logic.mint_annotation_collection(chart, name=model.name, ctx=ctx))
        return chart_logic.create_layer(chart, kind=enums.ChartLayerKindChoices.ANNOTATION.value, source=lens, **settings)


# --- points -------------------------------------------------------------------


_LENS_ROWS = "The table lens whose rows are read: a table's whole lens (`TableDataset.fullLens`), or a window of it"


class CreatePointsChartLayerInputModel(_Style):
    chart: str
    lens: str
    x_column: str
    y_column: str
    z_column: str | None = None


@prose_errors
@kante.pydantic_input(
    CreatePointsChartLayerInputModel,
    description=(
        "Input for drawing a table's rows as points in a VALUES chart: each row at the values of `xColumn` and `yColumn` (and `zColumn` for depth) -- nuclei at their "
        "`area` and `volume`. The rows are placed in the chart's world (whose rows); the columns' units are its drawn axes, and a second layer's must convert to them. Writes no edge"
    ),
)
class CreatePointsChartLayerInput:
    """Input for creating a points layer."""

    chart: strawberry.ID = strawberry.field(description="The VALUES chart to draw in: its world must be the lens' table's space, or one it is placed in")
    lens: strawberry.ID = strawberry.field(description=_LENS_ROWS)
    x_column: str = strawberry.field(description="The numeric column whose value is a row's horizontal position")
    y_column: str = strawberry.field(description="The numeric column whose value is a row's vertical position")
    z_column: str | None = strawberry.field(default=None, description="The numeric column whose value is a row's depth, for a three-dimensional scatter. Omit for a flat one")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="MARKERS (the default), or a LINE through the rows sorted along x")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def create_points_chart_layer(info: Info, input: CreatePointsChartLayerInput) -> types.PointsChartLayer:
    """Draw a table lens' rows at the values of two or three of its columns."""
    model = input.to_pydantic()
    chart = get_for_org(models.Chart, info, id=model.chart)
    lens = lens_of_kind(info, kind=core_models.TableLens, lens=model.lens, role="a points layer reads")

    with transaction.atomic():
        return chart_logic.create_layer(chart, kind=enums.ChartLayerKindChoices.POINTS.value, source=lens, x_column=model.x_column, y_column=model.y_column, z_column=model.z_column, **_settings(model))


# --- distribution -------------------------------------------------------------


class CreateDistributionChartLayerInputModel(_Style):
    chart: str
    lens: str
    value_column: str
    group_column: str | None = None
    bins: int | None = None


@prose_errors
@kante.pydantic_input(
    CreateDistributionChartLayerInputModel,
    description=(
        "Input for summarising the values of one column of a table's rows in a VALUES chart: a HISTOGRAM or a BOX, over every row or once per value of `groupColumn` "
        "-- the `area` of nuclei, one histogram per `condition`. The rows are placed in the chart's world (whose rows). Writes no edge"
    ),
)
class CreateDistributionChartLayerInput:
    """Input for creating a distribution layer."""

    chart: strawberry.ID = strawberry.field(description="The VALUES chart to draw in: its world must be the lens' table's space, or one it is placed in")
    lens: strawberry.ID = strawberry.field(description=_LENS_ROWS)
    value_column: str = strawberry.field(description="The numeric column whose values are summarised: the chart's horizontal axis")
    group_column: str | None = strawberry.field(default=None, description="A column that sorts rows -- a text or boolean attribute, a label, a group id -- or a long-form CHANNEL coordinate, drawn as one summary per value. Omit for one over every row")
    bins: int | None = strawberry.field(default=None, description="How many bins a HISTOGRAM is cut into. Omit to let the viewer choose; a BOX takes none")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="A HISTOGRAM (the default) or a BOX")


def create_distribution_chart_layer(info: Info, input: CreateDistributionChartLayerInput) -> types.DistributionChartLayer:
    """Summarise the values of one column of a table lens' rows."""
    model = input.to_pydantic()
    chart = get_for_org(models.Chart, info, id=model.chart)
    lens = lens_of_kind(info, kind=core_models.TableLens, lens=model.lens, role="a distribution reads")

    with transaction.atomic():
        return chart_logic.create_layer(chart, kind=enums.ChartLayerKindChoices.DISTRIBUTION.value, source=lens, value_column=model.value_column, group_column=model.group_column, bins=model.bins, **_settings(model))


# --- update / delete ----------------------------------------------------------


class UpdateChartLayerInputModel(_Style):
    id: str


@prose_errors
@kante.pydantic_input(
    UpdateChartLayerInputModel,
    description=(
        "Input for restyling a chart layer of any kind: view state only. What a layer reads -- its lens and its columns -- is changed by its kind's own update "
        "(updateTraceChartLayer, updateSeriesChartLayer, updatePointsChartLayer, updateDistributionChartLayer); where its data sits along the chart's axis is the "
        "graph's, changed by editing the registration"
    ),
)
class UpdateChartLayerInput:
    """Input for updating a chart layer's view state."""

    id: strawberry.ID = strawberry.field(description="The ID of the chart layer to update")
    name: str | None = strawberry.field(default=None, description="A new name. Omit to leave it as it is")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn. Omit to leave it as it is")
    order: int | None = strawberry.field(default=None, description="A new drawing order. Omit to leave it as it is")
    opacity: float | None = strawberry.field(default=None, description="A new alpha, 0 to 1. Omit to leave it as it is")
    color: list[float] | None = strawberry.field(default=None, description="A new RGBA colour. Omit to leave it as it is")
    mark: enums.ChartMark | None = strawberry.field(default=None, description="How the layer is drawn, among its kind's looks. Not for annotation layers. Omit to leave it as it is")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


#: The settings that describe how a line of values is drawn, which marks do not have.
_LINE_SETTINGS = ("mark", "line_width", "marker_size", "marker_shape")


def update_chart_layer(info: Info, input: UpdateChartLayerInput) -> types.ChartLayer:
    """Restyle a chart layer."""
    model = input.to_pydantic()
    layer = get_for_org(models.ChartLayer, info, id=model.id)

    settings = _settings(model)
    if layer.kind == enums.ChartLayerKindChoices.ANNOTATION.value:
        refused = [field for field in _LINE_SETTINGS if field in settings]
        if refused:
            raise ValueError(f"Chart layer {layer.pk} draws annotations, which have no {', '.join(refused)}: each mark carries its own styling.")
    allowed = enums.MARKS.get(str(layer.kind))
    if "mark" in settings and allowed is not None and settings["mark"] not in allowed:
        raise ValueError(f"A {str(layer.kind).upper()} layer is drawn as one of {', '.join(look.upper() for look in allowed)}, not {str(settings['mark']).upper()}: a look of another kind.")

    if settings:
        for field, value in settings.items():
            setattr(layer, field, value)
        with transaction.atomic():
            layer.save(update_fields=list(settings))
    return layer


# --- per-kind updates: what a layer reads, as the scene's layers have ------------

#: What each kind reads, and the update that changes it -- named in the refusal of the wrong one.
_KIND_VOCABULARY: dict[str, tuple[str, str]] = {
    enums.ChartLayerKindChoices.TRACE.value: ("an array lens read along the chart's axis", "updateTraceChartLayer"),
    enums.ChartLayerKindChoices.SERIES.value: ("a table lens and its value column", "updateSeriesChartLayer"),
    enums.ChartLayerKindChoices.ANNOTATION.value: ("a collection's marks, each carrying its own styling", "updateChartLayer"),
    enums.ChartLayerKindChoices.POINTS.value: ("a table lens and the columns of its x, y and z positions", "updatePointsChartLayer"),
    enums.ChartLayerKindChoices.DISTRIBUTION.value: ("a table lens, the column it summarises, a grouping and bins", "updateDistributionChartLayer"),
}


def _layer_of_kind(info: Info, id: str, expected: enums.ChartLayerKindChoices) -> "models.ChartLayer":
    """The layer, refused in prose when it is of another kind -- naming the update that kind takes."""
    layer = get_for_org(models.ChartLayer, info, id=id)
    if layer.kind == expected.value:
        return layer
    theirs, mutation = _KIND_VOCABULARY.get(str(layer.kind), ("other settings", "updateChartLayer"))
    mine, _ = _KIND_VOCABULARY[expected.value]
    raise ValueError(f"Chart layer {layer.pk} is a {str(layer.kind).upper()} layer, not a {expected.value.upper()} layer: it reads {theirs}, not {mine}. Use {mutation}.")


def _update(info: Info, layer: "models.ChartLayer", model: BaseModel, *, lens_kind: type, role: str, clear: frozenset[str] = frozenset(), **bindings: object) -> "models.ChartLayer":
    """Apply one kind's update: resolve the lens it names, then the shared rules in `chart_logic.update_layer`."""
    lens_id = getattr(model, "lens", None)
    source = lens_of_kind(info, kind=lens_kind, lens=lens_id, role=role) if lens_id else None
    with transaction.atomic():
        return chart_logic.update_layer(layer, source=source, clear=clear, **bindings, **_settings(model))


_LENS_REBIND = "A lens to read through instead: the data must be placed in the chart as creating the layer requires. Omit to keep the current one"


class UpdateTraceChartLayerInputModel(_Style):
    id: str
    lens: str | None = None


@prose_errors
@kante.pydantic_input(UpdateTraceChartLayerInputModel, description="Retune a trace: draw another array lens along the chart's axis, or change how it is drawn")
class UpdateTraceChartLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the trace layer to update")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_LENS_REBIND + ". An array lens leaving free the axis laid along the chart (and at most one CHANNEL or INDEX axis beside it)")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn")
    order: int | None = strawberry.field(default=None, description="A new drawing order")
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="LINE, MARKERS, LINE_MARKERS or STEPS")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def update_trace_chart_layer(info: Info, input: UpdateTraceChartLayerInput) -> types.TraceChartLayer:
    """Rebind or restyle a trace."""
    model = input.to_pydantic()
    layer = _layer_of_kind(info, model.id, enums.ChartLayerKindChoices.TRACE)
    return _update(info, layer, model, lens_kind=core_models.ArrayLens, role="a trace reads")


class UpdateSeriesChartLayerInputModel(_Style):
    id: str
    lens: str | None = None
    value_column: str | None = None


@prose_errors
@kante.pydantic_input(UpdateSeriesChartLayerInputModel, description="Retune a series: read another value column, or another table lens, against the chart's axis")
class UpdateSeriesChartLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the series layer to update")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_LENS_REBIND + ". A table lens; its value column is kept when the new table has it, else inferred")
    value_column: str | None = strawberry.field(default=None, description="The numeric column read as the value. Omit to keep the current one")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn")
    order: int | None = strawberry.field(default=None, description="A new drawing order")
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="LINE, MARKERS, LINE_MARKERS or STEPS")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def update_series_chart_layer(info: Info, input: UpdateSeriesChartLayerInput) -> types.SeriesChartLayer:
    """Rebind or restyle a series."""
    model = input.to_pydantic()
    layer = _layer_of_kind(info, model.id, enums.ChartLayerKindChoices.SERIES)
    return _update(info, layer, model, lens_kind=core_models.TableLens, role="a series reads", value_column=model.value_column)


class UpdatePointsChartLayerInputModel(_Style):
    id: str
    lens: str | None = None
    x_column: str | None = None
    y_column: str | None = None
    z_column: str | None = None
    clear_z_column: bool = False


@prose_errors
@kante.pydantic_input(UpdatePointsChartLayerInputModel, description="Retune a points layer: plot other columns, or another table lens' rows, or change how they are drawn")
class UpdatePointsChartLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the points layer to update")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_LENS_REBIND + ". A table lens whose rows are placed in the chart's world, holding the columns plotted")
    x_column: str | None = strawberry.field(default=None, description="The numeric column of the horizontal position. Omit to keep the current one")
    y_column: str | None = strawberry.field(default=None, description="The numeric column of the vertical position. Omit to keep the current one")
    z_column: str | None = strawberry.field(default=None, description="The numeric column of the depth (or colour). Omit to keep the current one")
    clear_z_column: bool = strawberry.field(default=False, description="Drop the z column: a flat scatter")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn")
    order: int | None = strawberry.field(default=None, description="A new drawing order")
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="MARKERS, or a LINE through the rows sorted along x")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)
    marker_size: float | None = strawberry.field(default=None, description=_MARKER_SIZE)
    marker_shape: enums.ChartMarkerShape | None = strawberry.field(default=None, description=_MARKER_SHAPE)


def update_points_chart_layer(info: Info, input: UpdatePointsChartLayerInput) -> types.PointsChartLayer:
    """Rebind or restyle a points layer."""
    model = input.to_pydantic()
    layer = _layer_of_kind(info, model.id, enums.ChartLayerKindChoices.POINTS)
    clear = frozenset({"z_column"} if model.clear_z_column else set())
    return _update(info, layer, model, lens_kind=core_models.TableLens, role="a points layer reads", clear=clear, x_column=model.x_column, y_column=model.y_column, z_column=model.z_column)


class UpdateDistributionChartLayerInputModel(_Style):
    id: str
    lens: str | None = None
    value_column: str | None = None
    group_column: str | None = None
    bins: int | None = None
    clear_group_column: bool = False
    clear_bins: bool = False


@prose_errors
@kante.pydantic_input(UpdateDistributionChartLayerInputModel, description="Retune a distribution: summarise another column, group it otherwise, bin it otherwise, or draw it as a histogram or a box")
class UpdateDistributionChartLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the distribution layer to update")
    lens: strawberry.ID | None = strawberry.field(default=None, description=_LENS_REBIND + ". A table lens whose rows are placed in the chart's world, holding the columns read")
    value_column: str | None = strawberry.field(default=None, description="The numeric column summarised. Omit to keep the current one")
    group_column: str | None = strawberry.field(default=None, description="The column whose values split the rows into one summary each. Omit to keep the current one")
    bins: int | None = strawberry.field(default=None, description="How many bins a HISTOGRAM is cut into. Omit to keep the current number")
    clear_group_column: bool = strawberry.field(default=False, description="Drop the grouping: one summary over every row")
    clear_bins: bool = strawberry.field(default=False, description="Drop the bin count: the viewer chooses")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn")
    order: int | None = strawberry.field(default=None, description="A new drawing order")
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    color: list[float] | None = strawberry.field(default=None, description=_COLOR)
    mark: enums.ChartMark | None = strawberry.field(default=None, description="A HISTOGRAM or a BOX. A box has no bins: switching to one drops the stored count")
    line_width: float | None = strawberry.field(default=None, description=_LINE_WIDTH)


def update_distribution_chart_layer(info: Info, input: UpdateDistributionChartLayerInput) -> types.DistributionChartLayer:
    """Rebind, regroup, rebin or restyle a distribution."""
    model = input.to_pydantic()
    layer = _layer_of_kind(info, model.id, enums.ChartLayerKindChoices.DISTRIBUTION)
    clear = frozenset({"group_column"} if model.clear_group_column else set()) | frozenset({"bins"} if model.clear_bins else set())
    return _update(
        info, layer, model, lens_kind=core_models.TableLens, role="a distribution reads", clear=clear, value_column=model.value_column, group_column=model.group_column, bins=model.bins
    )


class DeleteChartLayerInputModel(BaseModel):
    id: str = Field(description="The ID of the chart layer to delete")


@kante.pydantic_input(DeleteChartLayerInputModel, description="Input for deleting a chart layer by ID. Deletes the layer and nothing it drew")
class DeleteChartLayerInput:
    """Input for deleting a chart layer by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the chart layer to delete")


def _chart_owner(layer: "models.ChartLayer") -> tuple:
    """A layer is deleted by whoever may delete its chart."""
    return (layer.chart.creator_id,)


delete_chart_layer = make_delete(models.ChartLayer, DeleteChartLayerInput, owner=_chart_owner)
