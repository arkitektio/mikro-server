from kante.types import Info
import strawberry

from core import types, models, enums
import kante
from pydantic import BaseModel
from core.logic import attribute_plans as attribute_plans_logic
from core.logic import graph as graph_logic
from core.mutations import layer as layer_mutations
from core.render import color_by as color_by_models
from core.render import filter_by as filter_by_models
from core.render.layer import inputs as layer_inputs
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.scoping import get_for_org


def _resolve_table_dataset(info: Info, table_dataset_id: str) -> "models.TableDataset":
    """The table dataset a point/track layer draws from, checked for a place to draw it.

    The dataset is the whole mapping: its declared coordinate columns are the
    coordinates, its own coordinate system is the space, its column roles are the
    identities. Nothing is bound per layer -- a per-layer copy of any of it could
    disagree with the schema the dataset already declares.
    """
    dataset = get_for_org(models.TableDataset, info, id=table_dataset_id)
    spatial = [col for col in dataset.columns_by_role(enums.ColumnRoleChoices.COORDINATE.value) if col.axis_type == enums.AxisTypeChoices.SPACE.value]
    if len(spatial) < 2:
        raise ValueError(f"A point/track layer needs a table dataset with at least two SPACE coordinate columns, but '{dataset.name}' has {len(spatial)}.")
    return dataset


def point_reachable_tables(info: Info, table_dataset) -> dict:
    """The tables a point layer's colouring may read, keyed by id.

    A point layer differs from the other two kinds in exactly one way, and it is
    the whole of why this exists: **its objects ARE rows of a table**. A mask's
    pixels and a collection's surfaces are geometry that has to be dereferenced
    into record-land across a FIELD edge; a point already stands there. So the
    layer's own table is reachable without any edge at all, and it is seeded here
    rather than found by the walk, which would never return it.

    Everything further away is an ordinary FIELD walk from the table's own
    system, so a point layer can also be coloured by whatever its space reaches.
    """
    reachable = attribute_plans_logic.field_reachable_tables(
        table_dataset.coordinate_system_or_none, info.context.request.organization
    ) if table_dataset.coordinate_system_or_none else {}
    return {str(table_dataset.pk): table_dataset, **reachable}


def build_point_pickers(info: Info, table_dataset, color_bys, filter_bys):
    """Both pickers for a point layer, against one walk of its reachability."""
    reachable = point_reachable_tables(info, table_dataset)
    built_color_bys = layer_mutations.build_color_bys(
        info,
        table_dataset.coordinate_system_or_none,
        color_bys,
        source="this point cloud",
        entry_model=color_by_models.LabelColorByModel,
        # `build_color_bys` resolves the sparse walk itself, and roots it on the
        # system we hand it -- the table's own, which is exactly where a matrix
        # identified by this table is reachable from.
        reachable=reachable,
    )
    built_filter_bys = layer_mutations.build_filter_bys(
        info,
        table_dataset.coordinate_system_or_none,
        filter_bys,
        source="this point cloud",
        entry_model=filter_by_models.LabelFilterByModel,
        reachable=reachable,
    )
    return built_color_bys, built_filter_bys


class CreatePointLayerInputModel(BaseModel):
    scene: str
    table_dataset: str
    color_bys: list[layer_inputs.LabelColorByInputModel] | None = None
    active_color_by: int | None = None
    filter_bys: list[layer_inputs.LabelFilterByInputModel] | None = None
    active_filter_bys: list[int] | None = None
    size_column: str | None = None
    color_column: str | None = None
    point_size: float | None = None
    colormap: enums.ColorMap | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(CreatePointLayerInputModel, description="Create a layer that renders a point cloud (e.g. SMLM localisations, centroids) from a table dataset")
class CreatePointLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    color_bys: list[layer_inputs.LabelColorByInput] | None = strawberry.field(default=None, description="The colourings this layer offers, in the order a picker should show them. Each names a column of a table this layer's ids key into -- its own table needs no edge, since a point IS a row of it -- or one slice of a sparse matrix they index")
    active_color_by: int | None = strawberry.field(default=None, description="Which entry of `colorBys` is drawn, as an index into it. Null means every point takes the flat colour")
    filter_bys: list[layer_inputs.LabelFilterByInput] | None = strawberry.field(default=None, description="The filters this layer offers. Each keeps or drops points by a column of a table this layer's ids key into")
    active_filter_bys: list[int] | None = strawberry.field(default=None, description="Which entries of `filterBys` are applied, as indices into it. Combined with AND")
    table_dataset: strawberry.ID = strawberry.field(description="The ID of the table dataset whose declared coordinate columns provide the points. Its own coordinate system is the space and its column roles are the mapping -- no per-layer column binding exists")
    size_column: str | None = strawberry.field(default=None, description="The measure column mapped to per-point size -- a per-layer display choice among the dataset's columns")
    color_column: str | None = strawberry.field(default=None, description="The measure column mapped to per-point color/intensity (used with colormap)")
    point_size: float | None = strawberry.field(default=None, description="The default point size in scene units (default 3.0). A scene unit is the world's spatial-axis unit, and is a well-defined length only where the layer's `placementInvariance` is SIMILARITY or better")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap used to color points by their color_column (default 'viridis')")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_point_layer(info: Info, input: CreatePointLayerInput) -> types.PointLayer:
    """Create a point layer over a table dataset, refusing one the graph does not place."""
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    table_dataset = _resolve_table_dataset(info, model.table_dataset)

    graph_logic.assert_placeable_in(scene.world, table_dataset.coordinate_system_or_none, destination=f"the world of scene '{scene.name}'")

    color_bys, filter_bys = build_point_pickers(info, table_dataset, model.color_bys, model.filter_bys)
    layer_mutations.assert_active_color_by(color_bys or [], model.active_color_by, fallback="draw the flat point colour")
    layer_mutations.assert_active_filter_bys(filter_bys or [], model.active_filter_bys)

    return models.Layer.objects.create(
        kind=enums.LayerKind.POINT,
        scene=scene,
        table_dataset=table_dataset,
        point_color_bys=color_bys or [],
        active_color_by=model.active_color_by,
        point_filter_bys=filter_bys or [],
        active_filter_bys=model.active_filter_bys or [],
        size_column=model.size_column,
        color_column=model.color_column,
        point_size=model.point_size if model.point_size is not None else 3.0,
        colormap=model.colormap or enums.ColorMap.VIRIDIS,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )


class UpdatePointLayerInputModel(BaseModel):
    id: str
    color_bys: list[layer_inputs.LabelColorByInputModel] | None = None
    active_color_by: int | None = None
    filter_bys: list[layer_inputs.LabelFilterByInputModel] | None = None
    active_filter_bys: list[int] | None = None
    size_column: str | None = None
    point_size: float | None = None
    colormap: enums.ColorMap | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(UpdatePointLayerInputModel, description="Retune a point layer after creation -- above all, switch or republish its colour picker")
class UpdatePointLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the point layer to update")
    color_bys: list[layer_inputs.LabelColorByInput] | None = strawberry.field(default=None, description="Replaces the published picker wholesale -- its order is the display order, so there is nothing to merge on. Pass `[]` to remove every colouring; omit to leave it alone")
    active_color_by: int | None = strawberry.field(default=None, description="Which entry of `colorBys` is drawn. Re-checked against the picker being written, never the stored one")
    filter_bys: list[layer_inputs.LabelFilterByInput] | None = strawberry.field(default=None, description="Replaces the published filter picker wholesale. Pass `[]` to remove every rule")
    active_filter_bys: list[int] | None = strawberry.field(default=None, description="Which entries of `filterBys` are applied, as indices into it")
    size_column: str | None = strawberry.field(default=None, description="The measure column mapped to per-point size")
    point_size: float | None = strawberry.field(default=None, description="The default point size in scene units")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap used when a colouring names none")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha, from 0 to 1")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_point_layer(info: Info, input: UpdatePointLayerInput) -> types.PointLayer:
    """Retune a point layer, above all its pickers.

    The pickers are replaced **wholesale** rather than merged, exactly as they
    are on the other two kinds: their order is the display order, so there is
    nothing to merge on, and `[]` clears where omitted leaves alone. Everything
    else is an ordinary patch.
    """
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    if layer.kind != enums.LayerKind.POINT.value:
        raise ValueError(f"Layer {layer.pk} is a {layer.kind} layer, not a point layer, so it has no point size or point colouring to set.")

    color_bys, filter_bys = build_point_pickers(info, layer.table_dataset, model.color_bys, model.filter_bys)

    if color_bys is not None:
        layer.point_color_bys = color_bys
    if filter_bys is not None:
        layer.point_filter_bys = filter_bys
    # Checked against the picker being WRITTEN, never the stored one: a new
    # `colorBys` that no longer holds the active entry falls back rather than
    # pointing past the end.
    if model.active_color_by is not None:
        layer_mutations.assert_active_color_by(layer.point_color_bys or [], model.active_color_by, fallback="draw the flat point colour")
        layer.active_color_by = model.active_color_by
    elif color_bys is not None and layer.active_color_by is not None and layer.active_color_by >= len(layer.point_color_bys or []):
        layer.active_color_by = None
    if model.active_filter_bys is not None:
        layer_mutations.assert_active_filter_bys(layer.point_filter_bys or [], model.active_filter_bys)
        layer.active_filter_bys = model.active_filter_bys
    elif filter_bys is not None:
        layer.active_filter_bys = [index for index in (layer.active_filter_bys or []) if index < len(layer.point_filter_bys or [])]

    for field in ("size_column", "point_size", "colormap", "opacity", "visible", "order"):
        value = getattr(model, field)
        if value is not None:
            setattr(layer, field, value)

    layer.save()
    return layer


class CreateTrackLayerInputModel(BaseModel):
    scene: str
    table_dataset: str
    color_by_column: str | None = None
    line_width: float | None = None
    colormap: enums.ColorMap | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(CreateTrackLayerInputModel, description="Create a layer that renders trajectories (e.g. particle/cell tracks) from a table dataset, grouped by its TRACK_ID column")
class CreateTrackLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    table_dataset: strawberry.ID = strawberry.field(description="The ID of the table dataset whose declared coordinate + TRACK_ID columns provide the tracks")
    color_by_column: str | None = strawberry.field(default=None, description="The measure column used to color tracks (used with colormap) -- a per-layer display choice among the dataset's columns")
    line_width: float | None = strawberry.field(default=None, description="The width of the track lines in scene units (default 1.0). A scene unit is the world's spatial-axis unit, and is a well-defined length only where the layer's `placementInvariance` is SIMILARITY or better")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap used to color tracks by their color_by_column (default 'viridis')")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_track_layer(info: Info, input: CreateTrackLayerInput) -> types.TrackLayer:
    """Create a track layer over a table dataset, refusing one without tracks or a place."""
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    table_dataset = _resolve_table_dataset(info, model.table_dataset)

    if not table_dataset.columns_by_role(enums.ColumnRoleChoices.TRACK_ID.value):
        raise ValueError(f"Table dataset '{table_dataset.name}' has no TRACK_ID column, so it cannot be rendered as tracks.")

    graph_logic.assert_placeable_in(scene.world, table_dataset.coordinate_system_or_none, destination=f"the world of scene '{scene.name}'")

    return models.Layer.objects.create(
        kind=enums.LayerKind.TRACK,
        scene=scene,
        table_dataset=table_dataset,
        color_by_column=model.color_by_column,
        line_width=model.line_width if model.line_width is not None else 1.0,
        colormap=model.colormap or enums.ColorMap.VIRIDIS,
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )


class UpdateTrackLayerInputModel(BaseModel):
    id: str
    color_by_column: str | None = None
    line_width: float | None = None
    colormap: enums.ColorMap | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@kante.pydantic_input(UpdateTrackLayerInputModel, description="Retune a track layer after creation -- its line width, its colouring column and the compositing it takes part in")
class UpdateTrackLayerInput:
    id: strawberry.ID = strawberry.field(description="The ID of the track layer to update")
    color_by_column: str | None = strawberry.field(default=None, description="The measure column used to color tracks (used with colormap). A per-layer display choice among the dataset's columns -- never one of the coordinate or TRACK_ID columns, which the dataset declares by role")
    line_width: float | None = strawberry.field(default=None, description="The width of the track lines in scene units. A scene unit is the world's spatial-axis unit, and is a well-defined length only where the layer's `placementInvariance` is SIMILARITY or better")
    colormap: enums.ColorMap | None = strawberry.field(default=None, description="The colormap used to color tracks by their color_by_column")
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha, from 0 to 1")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")


def update_track_layer(info: Info, input: UpdateTrackLayerInput) -> types.TrackLayer:
    """Retune a track layer.

    An ordinary patch throughout, and deliberately smaller than its point
    sibling: a track layer publishes no `colorBys`/`filterBys` picker, only the
    flat `color_by_column`, so there is nothing to rebuild and re-check here.

    Which columns provide the coordinates, the time and the track identity is
    NOT settable -- the table dataset declares them by role, and a per-layer
    copy could disagree with the schema the dataset already publishes. That is
    the same rule `_resolve_table_dataset` states at creation.
    """
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    if layer.kind != enums.LayerKind.TRACK.value:
        raise ValueError(f"Layer {layer.pk} is a {layer.kind} layer, not a track layer, so it has no line width or track colouring to set.")

    for field in ("color_by_column", "line_width", "colormap", "blending", "opacity", "visible", "order"):
        value = getattr(model, field)
        if value is not None:
            setattr(layer, field, value)

    layer.save()
    return layer
