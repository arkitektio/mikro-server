from kante.types import Info
import strawberry

from core import types, models, enums
import kante
from pydantic import BaseModel
from core.logic import graph as graph_logic
from core.input_unions import prose_errors
from core.inputs.validators import Alpha
from core.mutations.layer import assert_active_color_by, assert_active_filter_bys, build_mesh_color_bys, build_mesh_filter_bys, mesh_reachable_tables
from core.render.layer import inputs as layer_inputs
from core.scoping import get_for_org


class CreateMeshLayerInputModel(BaseModel):
    scene: str
    mesh_collection: str
    material_color: list[int] | None = None
    wireframe: bool | None = None
    shading: enums.MeshShading | None = None
    max_level: int | None = None
    color_bys: list[layer_inputs.MeshColorByInputModel] | None = None
    active_color_by: int | None = None
    filter_bys: list[layer_inputs.MeshFilterByInputModel] | None = None
    active_filter_bys: list[int] | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


_COLOR_BYS_DESCRIPTION = (
    "The colourings this layer offers, in the order a picker should show them -- volume through a continuous colormap, cell type through a qualitative one -- instead of the flat `materialColor`. Each names a table "
    "reachable from this collection by a FIELD edge (author it with `createTableDataset(keyedBy: {kind: MESH_COLLECTION})`) and a column that table declares, because a colorBy naming an unrelated "
    "table is not a preference to hold onto until the edge shows up, it is a join nothing can execute. Which entry is drawn is `activeColorBy`; publishing a picker is not the same as choosing within it"
)

_ACTIVE_DESCRIPTION = "Which entry of `colorBys` is drawn, as an index into it. Null draws the flat `materialColor` -- what having no colouring has always meant"

_FILTER_BYS_DESCRIPTION = (
    "The filters this layer offers, in the order a picker should show them -- 'large cells', 'not debris' -- each keeping or dropping objects by a column of a table this collection's FIELD edge keys "
    "into. Which half of the rule applies follows from the column's declared role: `min`/`max` bounds over a measure column, an explicit `values` set over a categorical one. Two entries may share a "
    "column, because two ranges over one measure are two different rules. Which of them are actually applied is `activeFilterBys`"
)

_ACTIVE_FILTERS_DESCRIPTION = (
    "Which entries of `filterBys` are applied, as indices into it. Several at once is the normal case -- they combine with AND, and an object is drawn when every active rule keeps it. Empty applies "
    "none of them, so everything draws"
)

_MAX_LEVEL_DESCRIPTION = (
    "The deepest octree level this layer may load, capping detail against the collection's declared `grid.levels`. A budget, not a choice of level: which level a viewer fetches still follows from the "
    "zoom. Null lets the viewer decide"
)


def assert_max_level(collection, max_level: int | None) -> None:
    """Refuse an LOD cap the collection has no such level for.

    A cap past the last level is not a harmless over-estimate: it is a claim about a store,
    and the client that acts on it asks for a prefix nothing wrote. Skipped when the
    collection's manifest declares no level count -- there is then nothing to check against,
    and inventing a bound would refuse a legitimate store.
    """
    if max_level is None:
        return
    levels = (collection.grid or {}).get("levels")
    if levels is None:
        return
    levels = int(levels)
    if max_level >= levels:
        raise ValueError(f"Mesh collection {collection.pk} declares {levels} octree level(s), indexed 0..{levels - 1}, so `maxLevel` cannot be {max_level}.")


@prose_errors
@kante.pydantic_input(CreateMeshLayerInputModel, description="Create a layer that renders a mesh collection (surface reconstructions / isosurfaces) in a scene. The collection's own coordinate system is the layer's space, so it must already have a path to the scene's world")
class CreateMeshLayerInput:
    scene: strawberry.ID = strawberry.field(description="The ID of the scene to place the layer in")
    mesh_collection: strawberry.ID = strawberry.field(description="The ID of the mesh collection whose geometry this layer renders. Its own coordinate system is the layer's space")
    material_color: list[int] | None = strawberry.field(default=None, description="Material (surface) color of the mesh, as RGBA (default white)")
    wireframe: bool | None = strawberry.field(default=None, description="Whether to render the mesh as a wireframe (default false)")
    shading: enums.MeshShading | None = strawberry.field(default=None, description="How the surface is lit (default SMOOTH)")
    max_level: int | None = strawberry.field(default=None, description=_MAX_LEVEL_DESCRIPTION)
    color_bys: list[layer_inputs.MeshColorByInput] | None = strawberry.field(default=None, description=_COLOR_BYS_DESCRIPTION)
    active_color_by: int | None = strawberry.field(default=strawberry.UNSET, description=f"{_ACTIVE_DESCRIPTION}. Pass `null` to publish the picker and draw none of it; omit to leave the choice alone")
    filter_bys: list[layer_inputs.MeshFilterByInput] | None = strawberry.field(default=None, description=_FILTER_BYS_DESCRIPTION)
    active_filter_bys: list[int] | None = strawberry.field(default=None, description=_ACTIVE_FILTERS_DESCRIPTION)
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode (default 'normal', i.e. alpha-over)")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque). Default 1.0")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing (default true)")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing (default 0)")


def create_mesh_layer(info: Info, input: CreateMeshLayerInput) -> types.MeshLayer:
    model = input.to_pydantic()

    scene = get_for_org(models.Scene, info, id=model.scene)
    collection = get_for_org(models.MeshCollection, info, id=model.mesh_collection)

    graph_logic.assert_placeable_in(scene.world, getattr(collection, "coordinate_system", None), destination=f"the world of scene '{scene.name}'")

    assert_max_level(collection, model.max_level)
    # One walk of the FIELD edges for both pickers: they are two questions about the same
    # relation, and the coordinate graph does not need traversing twice to answer them.
    reachable = mesh_reachable_tables(info, collection) if (model.color_bys is not None or model.filter_bys is not None) else None
    color_bys = build_mesh_color_bys(info, collection, model.color_bys, reachable=reachable) or []
    assert_active_color_by(color_bys, model.active_color_by)
    filter_bys = build_mesh_filter_bys(info, collection, model.filter_bys, reachable=reachable) or []
    assert_active_filter_bys(filter_bys, model.active_filter_bys)

    return models.Layer.objects.create(
        kind=enums.LayerKind.MESH,
        scene=scene,
        mesh_collection=collection,
        material_color=model.material_color if model.material_color is not None else [255, 255, 255, 255],
        wireframe=model.wireframe if model.wireframe is not None else False,
        shading=model.shading or enums.MeshShading.SMOOTH,
        max_level=model.max_level,
        mesh_color_bys=color_bys,
        active_color_by=model.active_color_by,
        mesh_filter_bys=filter_bys,
        active_filter_bys=model.active_filter_bys or [],
        blending=model.blending or enums.Blending.NORMAL,
        opacity=model.opacity if model.opacity is not None else 1.0,
        visible=model.visible if model.visible is not None else True,
        order=model.order or 0,
    )


class UpdateMeshLayerInputModel(BaseModel):
    id: str
    material_color: list[int] | None = None
    wireframe: bool | None = None
    shading: enums.MeshShading | None = None
    max_level: int | None = None
    color_bys: list[layer_inputs.MeshColorByInputModel] | None = None
    active_color_by: int | None = None
    filter_bys: list[layer_inputs.MeshFilterByInputModel] | None = None
    active_filter_bys: list[int] | None = None
    blending: enums.Blending | None = None
    opacity: Alpha | None = None
    visible: bool | None = None
    order: int | None = None


@prose_errors
@strawberry.input(
    description=(
        "Retune how a mesh layer is drawn. A patch: an OMITTED field keeps its current value, so switching the colouring cannot silently drop the material or the wireframe -- while an explicit "
        "`null` CLEARS the fields whose null means something. The collection and the scene are not editable -- a layer renders what it was created to render"
    ),
)
class UpdateMeshLayerInput:
    """Plain `@strawberry.input`, for the reason `LabelRenderInput` gives.

    A pydantic-backed input takes its defaults from the model, so an `UNSET` written here would
    be decorative. This is the convention `_MAX_LEVEL_DESCRIPTION` asked for rather than
    inventing one for a single field."""

    id: strawberry.ID = strawberry.field(description="The ID of the mesh layer to update")
    material_color: list[int] | None = strawberry.field(default=None, description="Material (surface) color of the mesh, as RGBA")
    wireframe: bool | None = strawberry.field(default=None, description="Whether to render the mesh as a wireframe")
    shading: enums.MeshShading | None = strawberry.field(default=None, description="How the surface is lit")
    max_level: int | None = strawberry.field(
        default=strawberry.UNSET,
        description=f"{_MAX_LEVEL_DESCRIPTION}. Raising, lowering AND removing all work now: an omitted field keeps the cap, an explicit `null` removes it. That distinction used to be unavailable to a scalar -- the pickers escaped it by being lists, where `[]` is a value that says 'none'",
    )
    # The picker is replaced wholesale rather than merged: its order is the display order, so
    # there is no key to merge on that is not the order itself. That is also what finally makes
    # a colouring *removable* -- the standing limitation while this was a single object, where a
    # patch could not tell an omitted field from an explicit null.
    color_bys: list[layer_inputs.MeshColorByInput] | None = strawberry.field(
        default=None,
        description=f"{_COLOR_BYS_DESCRIPTION}. Replaces the published picker wholesale: its order is the display order, so there is nothing to merge on. Pass `[]` to remove every colouring and fall back to `materialColor`",
    )
    active_color_by: int | None = strawberry.field(
        default=strawberry.UNSET,
        description=f"{_ACTIVE_DESCRIPTION}. Pass `null` to publish the picker and draw none of it; omit to leave the choice alone. Re-checked against the picker being written, never the stored one. If a new `colorBys` no longer holds the entry that was active, the layer falls back to `materialColor` -- name `activeColorBy` in the same call to point at another entry instead",
    )
    filter_bys: list[layer_inputs.MeshFilterByInput] | None = strawberry.field(
        default=None,
        description=f"{_FILTER_BYS_DESCRIPTION}. Replaces the published filters wholesale, as `colorBys` does. Pass `[]` to remove every rule and draw all objects",
    )
    active_filter_bys: list[int] | None = strawberry.field(
        default=None,
        description=f"{_ACTIVE_FILTERS_DESCRIPTION}. Re-checked against the filters being written: a new `filterBys` that no longer holds an applied rule drops it from this set rather than leaving it dangling",
    )
    blending: enums.Blending | None = strawberry.field(default=None, description="Layer-level blend mode")
    opacity: float | None = strawberry.field(default=None, description="Layer alpha for alpha-over compositing, from 0 (transparent) to 1 (opaque)")
    visible: bool | None = strawberry.field(default=None, description="Whether the layer participates in compositing")
    order: int | None = strawberry.field(default=None, description="Explicit z-index for back-to-front compositing")

    def to_pydantic(self) -> UpdateMeshLayerInputModel:
        """Drop what the caller did not name, so `model_fields_set` records what it did."""
        supplied = {
            "id": self.id,
            "material_color": self.material_color,
            "wireframe": self.wireframe,
            "shading": self.shading,
            "max_level": self.max_level,
            "color_bys": self.color_bys,
            "active_color_by": self.active_color_by,
            "filter_bys": self.filter_bys,
            "active_filter_bys": self.active_filter_bys,
            "blending": self.blending,
            "opacity": self.opacity,
            "visible": self.visible,
            "order": self.order,
        }
        data = {name: value for name, value in supplied.items() if value is not strawberry.UNSET}
        for name in ("color_bys", "filter_bys"):
            entries = data.get(name)
            if entries:
                data[name] = [entry.to_pydantic() for entry in entries]
        return UpdateMeshLayerInputModel(**data)


def update_mesh_layer(info: Info, input: UpdateMeshLayerInput) -> types.MeshLayer:
    """Patch a mesh layer's render settings, leaving the ones not named alone."""
    model = input.to_pydantic()
    layer = get_for_org(models.Layer, info, id=model.id)
    if layer.kind != enums.LayerKind.MESH.value:
        raise ValueError(f"Layer {layer.pk} is a {layer.kind} layer, not a mesh layer, so it has no material, wireframe or object colouring to set.")

    named = model.model_fields_set
    if "max_level" in named:
        # Named, whether or not it is null. `assert_max_level` short-circuits on None, so
        # REMOVING a cap works now -- the thing this field's own description said it could not do.
        assert_max_level(layer.mesh_collection, model.max_level)
        layer.max_level = model.max_level

    reachable = mesh_reachable_tables(info, layer.mesh_collection) if (model.color_bys is not None or model.filter_bys is not None) else None

    color_bys = build_mesh_color_bys(info, layer.mesh_collection, model.color_bys, reachable=reachable)
    if color_bys is not None:
        layer.mesh_color_bys = color_bys
        # A shorter picker cannot leave the old index dangling, and clearing it entirely means
        # there is nothing to draw but the material color.
        if "active_color_by" not in named and layer.active_color_by is not None and layer.active_color_by >= len(color_bys):
            layer.active_color_by = None
    if "active_color_by" in named and model.active_color_by is None:
        # Named, and null: publish the picker and draw none of it.
        layer.active_color_by = None
    elif model.active_color_by is not None:
        assert_active_color_by(layer.mesh_color_bys or [], model.active_color_by)
        layer.active_color_by = model.active_color_by

    filter_bys = build_mesh_filter_bys(info, layer.mesh_collection, model.filter_bys, reachable=reachable)
    if filter_bys is not None:
        layer.mesh_filter_bys = filter_bys
        # The same fallback the colour picker takes, for the same reason: a patch cannot say
        # "and switch that one off", so a rule that is no longer published simply stops being
        # applied. The indices that survive keep pointing at what they pointed at, because the
        # list is replaced wholesale and never reordered under a caller.
        if model.active_filter_bys is None:
            layer.active_filter_bys = [index for index in (layer.active_filter_bys or []) if index < len(filter_bys)]
    if model.active_filter_bys is not None:
        assert_active_filter_bys(layer.mesh_filter_bys or [], model.active_filter_bys)
        layer.active_filter_bys = model.active_filter_bys

    if model.material_color is not None:
        layer.material_color = model.material_color
    if model.wireframe is not None:
        layer.wireframe = model.wireframe
    if model.shading is not None:
        layer.shading = model.shading
    if model.blending is not None:
        layer.blending = model.blending
    if model.opacity is not None:
        layer.opacity = model.opacity
    if model.visible is not None:
        layer.visible = model.visible
    if model.order is not None:
        layer.order = model.order
    layer.save()
    return layer
