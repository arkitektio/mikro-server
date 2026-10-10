"""Chart layers: one creator per kind, because each kind reads a different source.

Every creator runs the same gate before it writes -- the source must be laid along the
chart's axis by the graph -- and none of them writes an edge.
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

    @field_validator("color")
    @classmethod
    def _is_rgba(cls, color: list[float] | None) -> list[float] | None:
        if color is not None:
            assert_rgba(color, field="color")
        return color


def _settings(model: _Style) -> dict[str, object]:
    """The style a caller actually named, as column values."""
    named = {field: getattr(model, field) for field in ("name", "visible", "order", "opacity", "color", "line_width", "marker_size") if getattr(model, field) is not None}
    if model.mark is not None:
        named["mark"] = model.mark.value
    return named


_NAME = "A human-readable name for the layer"
_VISIBLE = "Whether the layer is drawn. Defaults to true"
_ORDER = "Explicit drawing order, back to front. Defaults to after the chart's last layer"
_OPACITY = "Layer alpha, from 0 (transparent) to 1 (opaque)"
_COLOR = "The colour the layer is drawn in, as RGBA: exactly four components. Omit to let the viewer choose"
_MARK = "How the values are drawn: a line, markers, both, or steps. A look, not a kind. Defaults to LINE"
_LINE_WIDTH = "Line width in screen pixels. Omit to let the viewer choose"
_MARKER_SIZE = "Marker size in screen pixels. Omit to let the viewer choose"


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


# --- update / delete ----------------------------------------------------------


class UpdateChartLayerInputModel(_Style):
    id: str


@prose_errors
@kante.pydantic_input(
    UpdateChartLayerInputModel,
    description=(
        "Input for restyling a chart layer. View state only: what the layer reads, and where its data sits along the chart's axis, are not settings -- the first is fixed "
        "at creation and the second is the graph's, changed by editing the registration"
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
    mark: enums.ChartMark | None = strawberry.field(default=None, description="How the values are drawn. Trace and series layers only. Omit to leave it as it is")
    line_width: float | None = strawberry.field(default=None, description="Line width in screen pixels. Trace and series layers only. Omit to leave it as it is")
    marker_size: float | None = strawberry.field(default=None, description="Marker size in screen pixels. Trace and series layers only. Omit to leave it as it is")


#: The settings that describe how a line of values is drawn, which marks do not have.
_LINE_SETTINGS = ("mark", "line_width", "marker_size")


def update_chart_layer(info: Info, input: UpdateChartLayerInput) -> types.ChartLayer:
    """Restyle a chart layer."""
    model = input.to_pydantic()
    layer = get_for_org(models.ChartLayer, info, id=model.id)

    settings = _settings(model)
    if layer.kind == enums.ChartLayerKindChoices.ANNOTATION.value:
        refused = [field for field in _LINE_SETTINGS if field in settings]
        if refused:
            raise ValueError(f"Chart layer {layer.pk} draws annotations, which have no {', '.join(refused)}: each mark carries its own styling.")

    if settings:
        for field, value in settings.items():
            setattr(layer, field, value)
        with transaction.atomic():
            layer.save(update_fields=list(settings))
    return layer


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
