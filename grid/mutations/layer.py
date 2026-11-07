"""Grid layers: one creator per kind, because each kind reads a different source. None writes an edge."""

import kante
import strawberry
from django.db import transaction
from kante.types import Info
from pydantic import BaseModel, Field

from core import enums as core_enums
from core import models as core_models
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.mutations._generic import make_delete
from core.mutations._sources import lens_of_kind
from core.scoping import get_for_org
from grid import enums, models, types
from grid.logic import grid as grid_logic


class _Style(BaseModel):
    """The view state every creator and the one update take. Omitted means the default, or 'leave it'."""

    name: str | None = None
    visible: bool | None = None
    order: int | None = None
    opacity: Alpha | None = None
    colormap: core_enums.ColorMap | None = None
    min: float | None = None
    max: float | None = None


def _settings(model: _Style) -> dict[str, object]:
    """The style a caller actually named, as column values."""
    named = {field: getattr(model, field) for field in ("name", "visible", "order", "opacity", "min", "max") if getattr(model, field) is not None}
    if model.colormap is not None:
        named["colormap"] = model.colormap.value
    return named


_NAME = "A human-readable name for the layer"
_VISIBLE = "Whether the layer is drawn. Defaults to true"
_ORDER = "Explicit drawing order, back to front. Defaults to after the grid's last layer"
_OPACITY = "Layer alpha, from 0 (transparent) to 1 (opaque)"
_COLORMAP = "How a value becomes a colour. Defaults to VIRIDIS"
_MIN = "The value drawn at the bottom of the colormap. Omit to let the viewer choose"
_MAX = "The value drawn at the top of the colormap. Omit to let the viewer choose"


class CreateHeatmapGridLayerInputModel(_Style):
    grid: str
    lens: str


@prose_errors
@kante.pydantic_input(
    CreateHeatmapGridLayerInputModel,
    description="Input for drawing an array or a sparse matrix in a grid: its lens leaves free exactly the grid's axes, placed in its world, and each position is a cell coloured by the value there",
)
class CreateHeatmapGridLayerInput:
    """Input for creating a heatmap layer."""

    grid: strawberry.ID = strawberry.field(description="The grid to draw in")
    lens: strawberry.ID = strawberry.field(description="The array or sparse lens to draw: a sparse dataset's whole lens, or an array lens sliced to the grid's axes")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    colormap: core_enums.ColorMap | None = strawberry.field(default=None, description=_COLORMAP)
    min: float | None = strawberry.field(default=None, description=_MIN)
    max: float | None = strawberry.field(default=None, description=_MAX)


def create_heatmap_grid_layer(info: Info, input: CreateHeatmapGridLayerInput) -> types.HeatmapGridLayer:
    """Draw an array or a sparse matrix in a grid, one cell per position."""
    model = input.to_pydantic()
    grid = get_for_org(models.Grid, info, id=model.grid)
    lens = lens_of_kind(info, kind=core_models.Lens, lens=model.lens, role="a heatmap reads")
    with transaction.atomic():
        return grid_logic.create_layer(grid, kind=enums.GridLayerKindChoices.HEATMAP.value, source=lens, **_settings(model))


class CreateCellsGridLayerInputModel(_Style):
    grid: str
    lens: str
    value_column: str | None = None


@prose_errors
@kante.pydantic_input(
    CreateCellsGridLayerInputModel,
    description="Input for drawing a table's rows in a grid: each row a cell at its position along the grid's axes -- the table's frame -- coloured by the value of `valueColumn`",
)
class CreateCellsGridLayerInput:
    """Input for creating a cells layer."""

    grid: strawberry.ID = strawberry.field(description="The grid to draw in")
    lens: strawberry.ID = strawberry.field(description="The table lens whose rows are drawn: a table's whole lens, or a window of it")
    value_column: str | None = strawberry.field(default=None, description="The numeric column colouring each cell. Omit when the table has exactly one")
    name: str | None = strawberry.field(default=None, description=_NAME)
    visible: bool | None = strawberry.field(default=None, description=_VISIBLE)
    order: int | None = strawberry.field(default=None, description=_ORDER)
    opacity: float | None = strawberry.field(default=None, description=_OPACITY)
    colormap: core_enums.ColorMap | None = strawberry.field(default=None, description=_COLORMAP)
    min: float | None = strawberry.field(default=None, description=_MIN)
    max: float | None = strawberry.field(default=None, description=_MAX)


def create_cells_grid_layer(info: Info, input: CreateCellsGridLayerInput) -> types.CellsGridLayer:
    """Draw a table's rows in a grid, one cell each."""
    model = input.to_pydantic()
    grid = get_for_org(models.Grid, info, id=model.grid)
    lens = lens_of_kind(info, kind=core_models.TableLens, lens=model.lens, role="cells read")
    with transaction.atomic():
        return grid_logic.create_layer(grid, kind=enums.GridLayerKindChoices.CELLS.value, source=lens, value_column=model.value_column, **_settings(model))


class UpdateGridLayerInputModel(_Style):
    id: str


@prose_errors
@kante.pydantic_input(UpdateGridLayerInputModel, description="Input for restyling a grid layer. View state only: what it reads is fixed at creation, and where it sits is the graph's")
class UpdateGridLayerInput:
    """Input for updating a grid layer's view state."""

    id: strawberry.ID = strawberry.field(description="The ID of the grid layer to update")
    name: str | None = strawberry.field(default=None, description="A new name. Omit to leave it as it is")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer is drawn. Omit to leave it as it is")
    order: int | None = strawberry.field(default=None, description="A new drawing order. Omit to leave it as it is")
    opacity: float | None = strawberry.field(default=None, description="A new alpha, 0 to 1. Omit to leave it as it is")
    colormap: core_enums.ColorMap | None = strawberry.field(default=None, description="A new colormap. Omit to leave it as it is")
    min: float | None = strawberry.field(default=None, description="A new bottom of the colormap. Omit to leave it as it is")
    max: float | None = strawberry.field(default=None, description="A new top of the colormap. Omit to leave it as it is")


def update_grid_layer(info: Info, input: UpdateGridLayerInput) -> types.GridLayer:
    """Restyle a grid layer."""
    model = input.to_pydantic()
    layer = get_for_org(models.GridLayer, info, id=model.id)
    settings = _settings(model)
    if settings:
        for field, value in settings.items():
            setattr(layer, field, value)
        with transaction.atomic():
            layer.save(update_fields=list(settings))
    return layer


class DeleteGridLayerInputModel(BaseModel):
    id: str = Field(description="The ID of the grid layer to delete")


@kante.pydantic_input(DeleteGridLayerInputModel, description="Input for deleting a grid layer by ID. Deletes the layer and nothing it drew")
class DeleteGridLayerInput:
    """Input for deleting a grid layer by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the grid layer to delete")


def _grid_owner(layer: "models.GridLayer") -> tuple:
    """A layer is deleted by whoever may delete its grid."""
    return (layer.grid.creator_id,)


delete_grid_layer = make_delete(models.GridLayer, DeleteGridLayerInput, owner=_grid_owner)
