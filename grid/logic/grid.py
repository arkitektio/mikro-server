"""What a grid is and what may be drawn in one. The rules, and the writers that obey them.

A grid shows *which*: its world's one or two enumerating axes (INDEX, CHANNEL, CATEGORY) are
its rows and columns, positions in order with no distance between them, and a value is drawn
as a cell's colour. Three questions decide whether a layer may exist:

1. **Is the data placed in the grid's world?** Mikro's strict rule
   (:func:`core.logic.graph.assert_placeable_in`): a route of edges that composes into one map.
2. **Does it cover the grid's axes?** A cell exists where the data has a position along the
   rows *and* the columns, so the composed map must reach every axis of the world.
3. **Does it have the shape its kind reads?** A heatmap leaves free exactly the grid's axes; a
   table's cells are coloured by a numeric column.
"""

from dataclasses import dataclass

from django.db import transaction

from core import enums as core_enums
from core import models as core_models
from core.creation import CreationContext
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from core.logic import placement
from grid import enums, models

#: The axis types a grid lays out: members, not measurements.
ENUMERATING_AXIS_TYPES = coords_logic.ENUMERATING_AXIS_TYPES

# --- the world ----------------------------------------------------------------


def grid_axes(world: "core_models.CoordinateSystem") -> list["core_models.Axis"]:
    """The axes a grid over this space lays out -- rows, then columns -- or the refusal if it cannot.

    One or two, because a screen has two directions to list members along. Enumerating, because
    a grid places nothing by distance: a measured axis is a scene's or a chart's, which is where
    the refusal points.
    """
    axes = list(world.axes.all())
    if not 1 <= len(axes) <= 2:
        raise ValueError(
            f"Coordinate system '{world.name}' has {len(axes)} axes ({', '.join(axis.name for axis in axes)}), and a grid lays out one or two: rows, and columns. "
            "Grid a space of the one or two enumerating axes the data is listed along."
        )
    measured = [axis for axis in axes if axis.type not in ENUMERATING_AXIS_TYPES]
    if measured:
        named = ", ".join(f"'{axis.name}' ({axis.type})" for axis in measured)
        raise ValueError(
            f"Coordinate system '{world.name}' has the measured axis {named}, and a grid lays out members -- INDEX, CHANNEL or CATEGORY positions, in order, with no distance "
            "between them. A measured axis is drawn by a scene (a place) or a chart (a quantity)."
        )
    return axes


def create_grid(*, name: str, world: "core_models.CoordinateSystem", ctx: CreationContext, description: str | None = None) -> "models.Grid":
    """Create a grid over an existing space: adopted as it is, never minted, never owned."""
    grid_axes(world)
    return models.Grid.objects.create(name=name, description=description, world=world, creator=ctx.user, organization=ctx.organization)


# --- placement ----------------------------------------------------------------


def layer_source_system(layer: "models.GridLayer") -> "core_models.CoordinateSystem":
    """The space a layer's data is expressed in: its lens' system."""
    return layer.lens.coordinate_system


def assert_covers(grid: "models.Grid", source_system: "core_models.CoordinateSystem", *, what: str) -> None:
    """Refuse a source not placed in the grid's world, or placed without a position along each of its axes."""
    world = grid.world
    graph_logic.assert_placeable_in(world, source_system, destination=f"the world of grid '{grid.name}'")
    condensed = placement.PlacementGraph(world, seed_systems=[source_system.pk]).condensed(source_system)
    reached = (set(condensed.output_axes) | set(condensed.fields)) if condensed is not None else set()
    missing = [axis.name for axis in grid_axes(world) if axis.name not in reached]
    if missing:
        raise ValueError(
            f"{what} is placed in the world of grid '{grid.name}', but its placement says nothing about {missing}. A cell is where the data has a position along the rows and "
            "the columns, so a layer covers every axis of the grid."
        )


# --- what each kind reads -----------------------------------------------------


def free_axes(lens: "core_models.Lens") -> list[str]:
    """The axes a lens leaves free: more than one position kept. What a heatmap spreads over the grid."""
    if isinstance(lens, core_models.ArrayLens):
        return [name for name, extent in zip(lens.axis_names, lens.shape_list) if extent > 1]
    if isinstance(lens, core_models.SparseLens):
        pinned = set(coords_logic.pinned_axes(lens.windows_list))
        names = lens.sparse_dataset.axis_names
        # A matrix whose store has not reported its shape yet: every axis a window does not pin.
        shape = lens.sparse_dataset.shape or [2] * len(names)
        return [name for name, extent in zip(names, shape) if name not in pinned and extent > 1]
    raise ValueError(f"Lens {lens.pk} is a {type(lens).__name__}; a heatmap reads an array or a sparse matrix.")


def assert_heatmap(grid: "models.Grid", lens: "core_models.Lens", *, what: str) -> None:
    """A heatmap's lens leaves free exactly as many axes as the grid lays out: one value per cell."""
    if not isinstance(lens, core_models.ArrayLens | core_models.SparseLens):
        raise ValueError(f"{what} is not an array or a sparse lens; a heatmap reads values at each position of an array or a sparse matrix. A table is drawn as CELLS.")
    free = free_axes(lens)
    grid_names = [axis.name for axis in grid_axes(grid.world)]
    if len(free) != len(grid_names):
        raise ValueError(
            f"{what} leaves {len(free)} axes free ({', '.join(free) or 'none'}), and grid '{grid.name}' lays out {len(grid_names)} ({', '.join(grid_names)}). A heatmap is one "
            "value per cell: slice the lens to one position along every other axis."
        )


def assert_cells(table: "core_models.TableDataset", value_column: str | None) -> str:
    """A table's cells are coloured by a numeric column of it: the one named, or the only one there could be."""
    numeric = [column.name for column in table.columns.all() if column.axis_type is None and column.role == core_enums.ColumnRoleChoices.ATTRIBUTE.value and coords_logic.is_numeric_dtype(column.dtype)]
    if value_column is None:
        if len(numeric) != 1:
            raise ValueError(
                f"Table dataset '{table.name}' has {len(numeric)} numeric attribute columns ({', '.join(numeric) or 'none'}), so which one colours its cells cannot be inferred. "
                "Name it with `valueColumn`."
            )
        return numeric[0]
    if value_column not in numeric:
        raise ValueError(f"Column '{value_column}' is not a numeric attribute column of table dataset '{table.name}'. A cell is coloured by one of: {', '.join(numeric) or 'none'}.")
    return value_column


# --- writers ------------------------------------------------------------------


def create_layer(grid: "models.Grid", *, kind: str, source: "core_models.Lens", value_column: str | None = None, order: int | None = None, **settings: object) -> "models.GridLayer":
    """Draw a lens in a grid, refusing one not placed over its axes or not its kind's shape. Appended after the last layer unless an order is named."""
    what = f"{type(source).__name__} {source.pk}"
    values: dict[str, object] = {}
    if kind == enums.GridLayerKindChoices.HEATMAP.value:
        if value_column is not None:
            raise ValueError("A HEATMAP's values are the array's or the matrix's own; `valueColumn` is how a table's CELLS are coloured.")
        assert_heatmap(grid, source, what=what)
    else:
        if not isinstance(source, core_models.TableLens):
            raise ValueError(f"{what} is not a table lens; CELLS draw a table's rows, one cell per row. An array or a sparse matrix is drawn as a HEATMAP.")
        values["value_column"] = assert_cells(source.table_dataset, value_column)
    assert_covers(grid, source.coordinate_system, what=what)

    if order is None:
        last = grid.layers.order_by("-order").values_list("order", flat=True).first()
        order = 0 if last is None else last + 1
    return models.GridLayer.objects.create(grid=grid, kind=kind, lens=source, order=order, **values, **settings)


# --- bootstrap ----------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """How a grid is drawn from what is already placed in a space."""

    #: At most this many layers.
    nchildren: int = 32


def _candidates(world: "core_models.CoordinateSystem", organization: object) -> list[tuple[str, object]]:
    """Everything strictly placed in a space that a grid layer could read, as ``(kind, container)``: matrices and arrays as heatmaps, tables as cells."""
    placed = graph_logic.placeable_system_ids_in(world) | {world.pk}
    found: list[tuple[str, object]] = []
    for matrix in core_models.SparseDataset.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.GridLayerKindChoices.HEATMAP.value, matrix))
    for dataset in core_models.ArrayDataset.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.GridLayerKindChoices.HEATMAP.value, dataset))
    for table in core_models.TableDataset.objects.filter(organization=organization, coordinate_system__in=placed).order_by("pk"):
        found.append((enums.GridLayerKindChoices.CELLS.value, table))
    return found


def bootstrap_grid_from_system(system: "core_models.CoordinateSystem", *, name: str | None, policy: Policy, ctx: CreationContext) -> "models.Grid":
    """A grid over an existing space, drawing what already lies over its axes. Authors no edges; a source its kind refuses is left out."""
    with transaction.atomic():
        grid = create_grid(name=name or system.name, world=system, ctx=ctx)
        drawn = 0
        for kind, container in _candidates(system, ctx.organization):
            if drawn >= policy.nchildren:
                break
            try:
                with transaction.atomic():
                    create_layer(grid, kind=kind, source=coordinate_system_logic.whole_lens(container), name=container.name)
            except ValueError:
                continue
            drawn += 1
    return grid
