"""The grid's GraphQL types. Every spatial answer here is derived from the graph on read."""

import datetime
from typing import List

import kante
from kante.types import Info
from strawberry import auto
from strawberry.scalars import JSON

from core import enums as core_enums
from core import types as core_types
from core.inputs.coords import CoordinateInput, at_map
from core.render import filter_by as filter_by_models
from core.render.layer.types import LabelFilterBy
from core.type_gen import create_stats_type
from core.types._shared import DESCRIPTORS_DESCRIPTION, OrgScoped, build_prescoped_queryset, resolve_descriptors
from core.types.auth import User
from core.types.coords import AffinePlacement, PlacementField
from grid import enums, filters, models
from grid.logic import grid as grid_logic
from grid.logic import grid_graph
from kanne_server import scalars as kanne_scalars


@kante.django_type(
    models.Grid,
    filters=filters.GridFilter,
    ordering=filters.GridOrder,
    pagination=True,
    description=(
        "A composition that shows **which**: its rows and columns are the members of its world's one or two enumerating axes (INDEX, CHANNEL, CATEGORY), in order and with "
        "no distance between them, and a value is drawn as a cell's colour. Beside a scene, which shows *where* (screen axes a place), and a chart, which shows *how much* "
        "(values as position): row 3 of a grid comes after row 2, it is not one unit from it. Examples: a sparse matrix of genes against cells as a heatmap; a long-form "
        "table's mean per cell and channel; a plate's wells. It adopts its world -- the space whose members it lays out -- names data through lenses and owns none of it"
    ),
)
class Grid(OrgScoped, core_types.Visualization):
    """A composition that lays out members along one or two enumerating axes."""

    descriptors: JSON = kante.django_field(resolver=resolve_descriptors, only=["stored_descriptors"], description=DESCRIPTORS_DESCRIPTION)

    id: auto
    name: auto
    description: str | None
    created_at: datetime.datetime
    creator: User | None
    layers: List["GridLayer"] = kante.django_field(
        filters=filters.GridLayerFilter,
        ordering=filters.GridLayerOrder,
        pagination=True,
        description="The layers drawn in this grid, back to front (a heterogeneous list of layer kinds)",
    )
    world_coordinate_system: core_types.CoordinateSystem = kante.django_field(
        field_name="world",
        description="The space whose members this grid lays out: one or two enumerating axes, rows then columns. Never owned by the grid: several compositions can share it, and deleting a grid never deletes it",
    )

    @kante.django_field(description="The axis whose members are the rows: the world's first axis")
    def row_axis(self, info: Info) -> core_types.Axis:
        """The world's first axis."""
        return grid_logic.grid_axes(self.world)[0]

    @kante.django_field(description="The axis whose members are the columns: the world's second axis. Null for a grid of one axis, a single column of rows")
    def column_axis(self, info: Info) -> core_types.Axis | None:
        """The world's second axis, if it has one."""
        axes = grid_logic.grid_axes(self.world)
        return axes[1] if len(axes) > 1 else None


@kante.django_interface(
    models.GridLayer,
    description=(
        "A layer drawn in a grid: a lens placed in the grid's world, covering its rows and columns, each cell coloured by a value. It carries view state only; where its data "
        "sits (`asAffine`, `placement`) is derived from the graph on read. The concrete kind says what it reads: HeatmapGridLayer an array or a sparse matrix, CellsGridLayer "
        "a table's rows"
    ),
)
class GridLayer:
    """A layer drawn in a grid, carrying the shared view state."""

    id: auto
    kind: enums.GridLayerKind
    name: str | None
    grid: Grid
    visible: bool
    order: int
    opacity: float
    colormap: core_enums.ColorMap = kante.django_field(description="How a value becomes a colour")
    min: float | None = kante.django_field(description="The value drawn at the bottom of the colormap. Null lets the viewer choose")
    max: float | None = kante.django_field(description="The value drawn at the top of the colormap. Null lets the viewer choose")

    @classmethod
    def get_queryset(cls, queryset, info, **kwargs):  # noqa: ANN001, ANN003, ANN206 - strawberry_django's hook
        """Scope to the request's organization, and select what the placement logic reads in Python."""
        queryset = build_prescoped_queryset(info, queryset)
        return queryset.select_related(*grid_graph.LAYER_PLACEMENT_RELATIONS).prefetch_related(*grid_graph.LAYER_SOURCE_AXIS_PREFETCH)

    @kante.django_field(
        description=(
            "This layer's placement composed into one affine map from its lens' space onto the grid's world: which of its positions is which row and column. Derived on read. "
            "Null when the layer is no longer placed"
        ),
    )
    def as_affine(self, info: Info, at: List[CoordinateInput] | None = None) -> AffinePlacement | None:
        """The layer's placement path composed into one labelled affine map."""
        condensed = grid_graph.for_request(info, self.grid).condensed(grid_logic.layer_source_system(self), at=at_map(at))
        if condensed is None:
            return None
        return AffinePlacement(
            matrix=condensed.matrix,
            input_axes=condensed.input_axes,
            output_axes=condensed.output_axes,
            total=condensed.total,
            fields=[PlacementField.from_form(axis, form, condensed.input_axes) for axis, form in condensed.fields.items()],
        )

    @kante.django_field(description="Whether this layer has a place in its grid's world, and if not, why not. Derived, never stored")
    def placement(self, info: Info, at: List[CoordinateInput] | None = None) -> core_enums.PlacementState:
        """PLACED, CONDITIONAL, UNREGISTERED or UNMAPPABLE."""
        return core_enums.PlacementState(grid_graph.for_request(info, self.grid).state(grid_logic.layer_source_system(self), at=at_map(at)))


@kante.django_type(
    models.GridLayer,
    filters=filters.GridLayerFilter,
    ordering=filters.GridLayerOrder,
    pagination=True,
    description=(
        "A layer that draws an array or a sparse matrix as a heatmap: its lens leaves free exactly the grid's axes, and each position is a cell coloured by the value there. "
        "Example: a sparse matrix of genes against cells, read through its whole lens"
    ),
)
class HeatmapGridLayer(GridLayer):
    """A layer that draws an array or a sparse matrix one cell per position."""

    id: auto
    lens: core_types.Lens = kante.django_field(disable_optimization=True, description="The array or sparse lens whose values are drawn")

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.GridLayer) and obj.kind == enums.GridLayerKind.HEATMAP.value


@kante.django_type(
    models.GridLayer,
    filters=filters.GridLayerFilter,
    ordering=filters.GridLayerOrder,
    pagination=True,
    description=(
        "A layer that draws a table's rows as cells: each row at its position along the grid's axes (the table's frame), coloured by the value of `valueColumn`. Example: a "
        "long-form `(cell, c, mean)` table, cells against channels coloured by `mean`; a plate's `(row, column)` wells coloured by a readout"
    ),
)
class CellsGridLayer(GridLayer):
    """A layer that draws a table's rows one cell each."""

    id: auto
    lens: core_types.TableLens = kante.django_field(disable_optimization=True, description="The table lens whose rows are drawn: the rows its windows keep")
    value_column: str = kante.django_field(description="The numeric column whose value colours each row's cell")

    @kante.django_field(description="The lens' windows as row filters, always applied. Derived from the lens; empty for a whole lens")
    def window_filters(self, info: Info) -> List[LabelFilterBy]:
        """The lens' windows as row filters."""
        del info
        return filter_by_models.filters_for_windows(str(self.lens.table_dataset_id), self.lens.windows_list)

    @kante.django_field(description="The table dataset whose rows are drawn: the container of the lens")
    def table_dataset(self, info: Info) -> core_types.TableDataset:
        """The lens' table."""
        return self.lens.table_dataset

    @kante.django_field(description="The unit of `valueColumn`, as the table declares it. Null when the column declares none")
    def value_unit(self, info: Info) -> kanne_scalars.Unit | None:
        column = next((column for column in self.lens.table_dataset.columns.all() if column.name == self.value_column), None)
        return column.unit if column is not None else None

    @classmethod
    def is_type_of(cls, obj, info) -> bool:  # noqa: ANN001, ANN206 - strawberry's hook
        return isinstance(obj, models.GridLayer) and obj.kind == enums.GridLayerKind.CELLS.value


#: The concrete layer types, handed to the schema's ``types=`` list.
grid_layer_types = [HeatmapGridLayer, CellsGridLayer]

GridStats, GridStatsResolver = create_stats_type(
    models.Grid,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields={"created_at": "created_at"},
    filters=filters.GridFilter,
)

__all__ = ["CellsGridLayer", "Grid", "GridLayer", "GridStats", "GridStatsResolver", "HeatmapGridLayer", "grid_layer_types"]
