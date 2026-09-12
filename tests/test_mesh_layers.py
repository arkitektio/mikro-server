"""Colouring a mesh layer by columns of the tables its collection keys into.

The same display pick a label layer makes with `colorBy`, over the same relation: a
`FIELD` edge from the collection into a table of per-object rows, authored by
`createTableDataset(keyedBy: {kind: MESH_COLLECTION})`. What differs is only where the id
was materialised -- on the geometry rows rather than in pixels -- which is invisible from
here, and is exactly the point: the check is one function for both layer kinds.

Where a mask holds one colouring, a collection publishes a **picker**: `colorBys` is an
ordered list an author offers and `activeColorBy` is the index of the one drawn. So the
list's invariants are on trial here alongside each entry's -- that two entries cannot
resolve to one column, that the index cannot point past the end, and that emptying the
list is a thing a patch can say at all, which it never was while this was one nullable
object.

The refusals matter more than the happy path. A colouring naming a table nothing reaches
is not a preference a client can hold onto until the edge shows up; it is a join nothing
can execute, and it would sit in the column looking valid until a renderer tried it.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import attribute_plans as attribute_plans_logic
from mikro_server.schema import schema
from tests import seed

LAYER_FIELDS = """
    id
    materialColor
    wireframe
    shading
    maxLevel
    activeColorBy
    activeFilterBys
    colorBys { table column colormap min max label joinPath { table column } }
    colorBy { table column colormap min max label joinPath { table column } }
    filterBys { table column min max values exclude label joinPath { table column } }
"""

CREATE_LAYER = """
mutation Create($input: CreateMeshLayerInput!) {
  createMeshLayer(input: $input) {
    %s
  }
}
""" % LAYER_FIELDS

UPDATE_LAYER = """
mutation Update($input: UpdateMeshLayerInput!) {
  updateMeshLayer(input: $input) {
    opacity
    %s
  }
}
""" % LAYER_FIELDS

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id }
}
"""

ZYX_MESH_AXES = [{"name": "z", "type": "SPACE"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]

#: One measure column and one categorical one, so both halves of the role split are testable
#: off a single table.
SHAPE_COLUMNS = [
    {"name": "object", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "volume", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    {"name": "cell_type", "dtype": "VARCHAR", "role": "LABEL"},
]


async def _collection(ctx: HttpContext) -> models.MeshCollection:
    """A mesh collection in a space of its own, registered into a scene's world."""
    store = await seed.create_fabriks_store(ctx)
    result = await schema.execute(
        "mutation Create($input: CreateMeshCollectionInput!) { createMeshCollection(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"version": "v1", "store": str(store.pk), "axes": ZYX_MESH_AXES}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.MeshCollection.objects.get)(id=result.data["createMeshCollection"]["id"])


async def _scene_for(ctx: HttpContext, collection: models.MeshCollection) -> models.Scene:
    """A scene the collection is placed in, so the layer mutation's placement gate passes."""
    scene = await seed.create_scene(ctx, "Composition")

    def register() -> None:
        models.Transformation.objects.create(
            kind="IDENTITY",
            input=collection.coordinate_system,
            output=scene.world,
            params={},
            organization=ctx.request.organization,
        )

    await sync_to_async(register)()
    return scene


async def _table(ctx: HttpContext, name: str, columns: list[dict], **extra: object) -> str:
    store = await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{name}", bucket="parquet", key=name, organization=ctx.request.organization, populated=True)
    result = await schema.execute(
        CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, columns, **extra)},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _create_layer(ctx: HttpContext, scene: models.Scene, collection: models.MeshCollection, **extra: object):
    return await schema.execute(
        CREATE_LAYER,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.pk), "meshCollection": str(collection.pk), **extra}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_layer_colours_by_a_keyed_table(authenticated_context: HttpContext):
    """The whole point of keying a table by a collection, seen from the renderer's end."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"}],
        activeColorBy=0,
    )
    assert not result.errors, result.errors

    layer = result.data["createMeshLayer"]
    assert layer["colorBys"] == [{"table": table, "column": "volume", "colormap": "VIRIDIS", "min": None, "max": None, "label": "Volume", "joinPath": []}]
    assert layer["activeColorBy"] == 0
    assert layer["colorBy"] == layer["colorBys"][0], "the derived field is the active entry, never a second copy of it"
    assert layer["materialColor"] == [255, 255, 255, 255], "the material is still there; colouring by a column does not erase it"

    # The column stores the enum's *value*, which is the lowercase name the renderer uses;
    # the SDL reports the member. Asserted on both sides because the dump is what a
    # renderer reads and the response is what a client caches.
    stored = await models.Layer.objects.aget(id=layer["id"])
    # `kind` is stored and defaults to "column", which is what makes sparse colourings a
    # migration-free addition: every entry written before they existed reads back as one of
    # these, because `ColorByModel(**entry)` fills the default. `dataset`/`at` are the other
    # variant's fields, null here -- and `attribute`/`target` are the GRAPH variant's, null
    # for the same reason on every non-network entry.
    assert stored.mesh_color_bys == [
        {
            "kind": "COLUMN",
            "table": table,
            "column": "volume",
            "dataset": None,
            "at": [],
            "attribute": None,
            "target": None,
            "join_path": [],
            "colormap": "viridis",
            "min": None,
            "max": None,
            "label": "Volume",
        }
    ]
    assert stored.active_color_by == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_picker_keeps_the_order_it_was_published_in(authenticated_context: HttpContext):
    """Several ways to read one collection, offered at once -- the thing a single `colorBy` could not do.

    The order is the display order, so it is asserted rather than treated as a set: a picker
    that reshuffles between reads is a menu whose items move under the cursor.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"},
            {"table": table, "column": "volume", "colormap": "MAGMA", "label": "Volume"},
        ],
        activeColorBy=1,
    )
    assert not result.errors, result.errors

    layer = result.data["createMeshLayer"]
    assert [entry["column"] for entry in layer["colorBys"]] == ["cell_type", "volume"]
    assert [entry["label"] for entry in layer["colorBys"]] == ["Cell type", "Volume"]
    assert layer["colorBy"]["column"] == "volume", "the second entry is the one being drawn"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_layer_without_a_colour_by_reads_back_empty(authenticated_context: HttpContext):
    """An empty picker and a null index mean the material colour is what is drawn.

    The shape every row written before the picker existed carries: a layer that offers
    nothing is not a layer offering one empty colouring, which a renderer would try to
    execute.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(authenticated_context, scene, collection, materialColor=[10, 20, 30, 255])
    assert not result.errors, result.errors
    layer = result.data["createMeshLayer"]
    assert layer["colorBys"] == []
    assert layer["activeColorBy"] is None
    assert layer["colorBy"] is None
    assert layer["materialColor"] == [10, 20, 30, 255]
    assert layer["shading"] == "SMOOTH", "the default flatters an isosurface; nothing was said, so nothing was chosen"
    assert layer["maxLevel"] is None, "no cap means the viewer decides"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_layer_refuses_a_table_no_field_edge_reaches(authenticated_context: HttpContext):
    """A join nothing can execute is refused where it is written, not where it is drawn."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    unrelated = await _table(authenticated_context, "unrelated", SHAPE_COLUMNS)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "volume", "colormap": "VIRIDIS"},
            {"table": unrelated, "column": "volume", "colormap": "VIRIDIS"},
        ],
    )
    assert result.errors
    message = str(result.errors[0])
    assert "not reachable from this collection by a FIELD edge" in message
    assert "keyedBy" in message, "point at the call that would make it reachable"
    assert "colorBys[1]" in message, "say which entry, because 'some table' is not actionable when five were sent"
    assert not await sync_to_async(models.Layer.objects.exists)(), "the whole picker is refused, not the reachable half of it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_layer_refuses_an_unknown_column(authenticated_context: HttpContext):
    """The table is reachable, but nothing in it is called that."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "sphericity", "colormap": "VIRIDIS"}])
    assert result.errors
    assert "declares no column 'sphericity'" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_entries_that_render_identically_are_refused(authenticated_context: HttpContext):
    """A picker whose two rows render identically asks someone to choose between a thing and itself.

    What counts as a repeat is the whole rendering, not the column: two colormaps over one
    measure are two colourings someone might genuinely switch between, and only entries
    agreeing on column, colormap and class colours are the same thing twice. The caption is
    not part of it -- a second name is not a second colouring. Refused rather than
    deduplicated: dropping the later one silently would renumber `activeColorBy` under a
    caller who counted the entries they sent.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    two_palettes = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"},
            {"table": table, "column": "volume", "colormap": "MAGMA", "label": "Volume, warmer"},
        ],
    )
    assert not two_palettes.errors, "one measure through two colormaps is two colourings, not one repeated"

    repeated = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"},
            {"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume again"},
        ],
    )
    assert repeated.errors
    message = str(repeated.errors[0])
    assert "exactly as colorBys[0] does" in message
    assert "one colouring wearing two names" in message, "the caption is not what a colouring is"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_active_index_that_points_at_nothing_is_refused(authenticated_context: HttpContext):
    """An index past the end is a claim about a picker that has no such entry."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    out_of_range = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS"}],
        activeColorBy=1,
    )
    assert out_of_range.errors
    assert "indexed 0..0" in str(out_of_range.errors[0])

    without_a_picker = await _create_layer(authenticated_context, scene, collection, activeColorBy=0)
    assert without_a_picker.errors
    assert "publishes no colourings to index into" in str(without_a_picker.errors[0])

    # A negative index is a valid Int and a valid Python one: `colorBys[-1]` would quietly draw
    # the last entry, so the caller gets someone else's colouring rather than an error.
    from_the_end = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS"}],
        activeColorBy=-1,
    )
    assert from_the_end.errors
    assert "counts from 0" in str(from_the_end.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_column_role_decides_which_colouring_applies(authenticated_context: HttpContext):
    """A measure column takes a colormap; a categorical one takes an explicit map, never both ways.

    The same rule label layers hold to, and it has to be checked here too rather than
    inherited: a colouring is validated against the table, and the table is the thing that
    knows which of the two a column is.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    measured_with_classes = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "volume", "colormap": "HUES"}])
    assert measured_with_classes.errors
    assert "coloured by a continuous colormap over their range" in str(measured_with_classes.errors[0])

    categorical_with_colormap = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "cell_type", "colormap": "VIRIDIS"}])
    assert categorical_with_colormap.errors
    assert "impose an order they do not have" in str(categorical_with_colormap.errors[0])

    categorical_with_classes = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "cell_type", "colormap": "HUES"}])
    assert not categorical_with_classes.errors, categorical_with_classes.errors
    assert categorical_with_classes.data["createMeshLayer"]["colorBys"][0]["colormap"] == "HUES"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colouring_can_window_its_colormap(authenticated_context: HttpContext):
    """`min`/`max` pin the colormap's ends to values, instead of stretching over one outlier.

    The window is part of the rendering, which is what the last assertion says: one measure
    through one colormap over two windows is two colourings, not one repeated, exactly as two
    colormaps over one measure are.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "min": 100.0, "max": 500.0, "label": "Volume"}],
        activeColorBy=0,
    )
    assert not result.errors, result.errors

    layer = result.data["createMeshLayer"]
    assert layer["colorBys"] == [{"table": table, "column": "volume", "colormap": "VIRIDIS", "min": 100.0, "max": 500.0, "label": "Volume", "joinPath": []}]

    stored = await models.Layer.objects.aget(id=layer["id"])
    assert stored.mesh_color_bys[0]["min"] == 100.0 and stored.mesh_color_bys[0]["max"] == 500.0

    # One end open is a window too: "everything above 500 is the top of the map".
    half_open = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "max": 500.0}])
    assert not half_open.errors, half_open.errors

    two_windows = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"},
            {"table": table, "column": "volume", "colormap": "VIRIDIS", "min": 100.0, "max": 500.0, "label": "Volume, mid-range"},
        ],
    )
    assert not two_windows.errors, "one measure through one colormap over two windows is two colourings, not one repeated"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_that_is_not_one_is_refused(authenticated_context: HttpContext):
    """The three ways `min`/`max` can fail to mean anything, each named for what it is."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    inverted = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "min": 500.0, "max": 100.0}])
    assert inverted.errors
    assert "cannot exceed `max`" in str(inverted.errors[0])

    # A qualitative colormap has no bottom or top to window -- it already answers what every
    # value looks like. Shape, so refused before the table is ever consulted.
    windowed_classes = await _create_layer(
        authenticated_context, scene, collection, colorBys=[{"table": table, "column": "cell_type", "colormap": "HUES", "min": 1.0}]
    )
    assert windowed_classes.errors
    assert "no bottom or top to window" in str(windowed_classes.errors[0])

    # A bare window over a categorical column would slip past the colormap check -- there is
    # no colormap named -- so the role check has to catch the bounds themselves.
    categorical_window = await _create_layer(authenticated_context, scene, collection, colorBys=[{"table": table, "column": "cell_type", "min": 1.0, "max": 2.0}])
    assert categorical_window.errors
    assert "a `min`/`max` window would impose an order they do not have" in str(categorical_window.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_level_cap_is_checked_against_the_collections_own_grid(authenticated_context: HttpContext):
    """A cap past the last level is a claim about a store, and the client acting on it fetches nothing."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    assert collection.grid["levels"] == 3, "the seeded manifest declares levels 0..2"

    capped = await _create_layer(authenticated_context, scene, collection, maxLevel=2)
    assert not capped.errors, capped.errors
    assert capped.data["createMeshLayer"]["maxLevel"] == 2

    too_deep = await _create_layer(authenticated_context, scene, collection, maxLevel=3)
    assert too_deep.errors
    assert "indexed 0..2" in str(too_deep.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_updating_the_colouring_keeps_everything_not_named(authenticated_context: HttpContext):
    """A patch, not a replacement: retuning the colouring must not drop the material.

    Without an update mutation the only way to recolour was to delete the layer and create
    it again, which loses its place in the scene's compositing order -- and a create-shaped
    update would quietly reset `materialColor` and `wireframe` to their defaults.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    created = await _create_layer(authenticated_context, scene, collection, materialColor=[10, 20, 30, 255], wireframe=True, opacity=0.5)
    assert not created.errors, created.errors
    layer_id = created.data["createMeshLayer"]["id"]

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "colorBys": [{"table": table, "column": "volume", "colormap": "MAGMA"}], "activeColorBy": 0}},
    )
    assert not result.errors, result.errors

    updated = result.data["updateMeshLayer"]
    assert updated["colorBys"] == [{"table": table, "column": "volume", "colormap": "MAGMA", "min": None, "max": None, "label": None, "joinPath": []}]
    assert updated["materialColor"] == [10, 20, 30, 255], "omitted, so unchanged"
    assert updated["wireframe"] is True, "omitted, so unchanged"
    assert updated["opacity"] == 0.5, "omitted, so unchanged"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_retuning_the_shading_leaves_the_picker_alone(authenticated_context: HttpContext):
    """The patch rule, from the other side: naming one render field must not clear the others."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"}],
        activeColorBy=0,
        maxLevel=1,
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "shading": "UNLIT"}},
    )
    assert not result.errors, result.errors

    updated = result.data["updateMeshLayer"]
    assert updated["shading"] == "UNLIT"
    assert updated["colorBys"] == created.data["createMeshLayer"]["colorBys"], "omitted, so unchanged"
    assert updated["activeColorBy"] == 0
    assert updated["maxLevel"] == 1, "omitted, so unchanged"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_empty_picker_removes_the_colouring(authenticated_context: HttpContext):
    """The limitation the single nullable `colorBy` could never fix: taking one back.

    A patch cannot tell an omitted field from an explicit null, so a colouring once set could
    only be removed by recreating the layer. A list can say "none" out loud, and the index
    falls back with it -- there is nothing left for it to point at.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS"}],
        activeColorBy=0,
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "colorBys": []}},
    )
    assert not result.errors, result.errors

    updated = result.data["updateMeshLayer"]
    assert updated["colorBys"] == []
    assert updated["activeColorBy"] is None, "nothing is left to draw, so the material color is what is drawn"
    assert updated["colorBy"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_shortening_the_picker_past_the_active_entry_falls_back(authenticated_context: HttpContext):
    """Dropping the entry that was being drawn leaves the material colour, not a dangling index.

    The index is re-checked against the picker being *written*, and there is no third option
    here: a patch cannot say "and set activeColorBy to null", so refusing would make a
    two-entry picker impossible to shrink while its second entry was active.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[
            {"table": table, "column": "volume", "colormap": "VIRIDIS"},
            {"table": table, "column": "cell_type", "colormap": "HUES"},
        ],
        activeColorBy=1,
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "colorBys": [{"table": table, "column": "volume", "colormap": "VIRIDIS"}]}},
    )
    assert not result.errors, result.errors

    updated = result.data["updateMeshLayer"]
    assert [entry["column"] for entry in updated["colorBys"]] == ["volume"]
    assert updated["activeColorBy"] is None, "the entry it pointed at is gone, so nothing is drawn but the material"
    assert updated["colorBy"] is None

    # Naming the new index in the same call is how you keep something drawn.
    repointed = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "activeColorBy": 0}},
    )
    assert not repointed.errors, repointed.errors
    assert repointed.data["updateMeshLayer"]["colorBy"]["column"] == "volume"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_publishes_filters_and_applies_the_ones_switched_on(authenticated_context: HttpContext):
    """The colour picker's sibling: which objects are drawn, decided by the same joined table.

    Two rules over one column on purpose -- 'small' and 'large' are two different filters over
    one measure, which is exactly what a picker is for and exactly what the colour picker
    refuses, because two identical colourings are one colouring twice.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        filterBys=[
            {"table": table, "column": "volume", "max": 100.0, "label": "Small"},
            {"table": table, "column": "volume", "min": 500.0, "label": "Large"},
            {"table": table, "column": "cell_type", "values": ["debris"], "exclude": True, "label": "Not debris"},
        ],
        activeFilterBys=[1, 2],
    )
    assert not result.errors, result.errors

    layer = result.data["createMeshLayer"]
    assert [entry["label"] for entry in layer["filterBys"]] == ["Small", "Large", "Not debris"]
    assert layer["filterBys"][0] == {"table": table, "column": "volume", "min": None, "max": 100.0, "values": None, "exclude": False, "label": "Small", "joinPath": []}
    assert layer["filterBys"][2] == {"table": table, "column": "cell_type", "min": None, "max": None, "values": ["debris"], "exclude": True, "label": "Not debris", "joinPath": []}
    assert layer["activeFilterBys"] == [1, 2], "two rules on at once, ANDed -- the case an index could not express"

    stored = await models.Layer.objects.aget(id=layer["id"])
    assert stored.active_filter_bys == [1, 2]
    assert stored.mesh_filter_bys[1]["min"] == 500.0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_without_filters_draws_everything(authenticated_context: HttpContext):
    """Empty and empty: nothing is offered, nothing is applied, and no object is hidden."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    result = await _create_layer(authenticated_context, scene, collection)
    assert not result.errors, result.errors
    assert result.data["createMeshLayer"]["filterBys"] == []
    assert result.data["createMeshLayer"]["activeFilterBys"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_column_role_decides_which_rule_a_filter_may_carry(authenticated_context: HttpContext):
    """Bounds over a measure, a value set over a categorical -- the split `colorBy` already turns on.

    Checked against the table, because the table is the only thing that knows which of the two
    a column is. The shape rules the input carries on its own -- one kind of rule at a time, a
    range that is not empty -- are checked before it ever gets here.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    measured_with_values = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": table, "column": "volume", "values": ["3"]}])
    assert measured_with_values.errors
    assert "filtered by a `min`/`max` range" in str(measured_with_values.errors[0])

    categorical_with_bounds = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": table, "column": "cell_type", "min": 1.0}])
    assert categorical_with_bounds.errors
    assert "impose an order they do not have" in str(categorical_with_bounds.errors[0])

    both_kinds = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": table, "column": "volume", "min": 1.0, "values": ["3"]}])
    assert both_kinds.errors
    assert "never both" in str(both_kinds.errors[0])

    neither = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": table, "column": "volume"}])
    assert neither.errors
    assert "matches every row, which is not a filter" in str(neither.errors[0])

    empty_range = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": table, "column": "volume", "min": 500.0, "max": 100.0}])
    assert empty_range.errors
    assert "which is an empty range" in str(empty_range.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_filter_over_a_table_nothing_reaches_is_refused(authenticated_context: HttpContext):
    """The same join check the colouring gets, from the same function, with the index in the message."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    unrelated = await _table(authenticated_context, "unrelated", SHAPE_COLUMNS)

    result = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": unrelated, "column": "volume", "min": 1.0}])
    assert result.errors
    message = str(result.errors[0])
    assert "filterBys[0]" in message
    assert "not reachable from this collection by a FIELD edge" in message

    unknown_column = await _create_layer(authenticated_context, scene, collection, filterBys=[{"table": unrelated, "column": "nope", "min": 1.0}])
    assert unknown_column.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_active_filter_index_that_points_at_nothing_is_refused(authenticated_context: HttpContext):
    """And a rule named twice is refused too: applying one filter twice narrows nothing."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})
    rule = {"table": table, "column": "volume", "min": 100.0}

    out_of_range = await _create_layer(authenticated_context, scene, collection, filterBys=[rule], activeFilterBys=[1])
    assert out_of_range.errors
    assert "indexed 0..0" in str(out_of_range.errors[0])

    without_a_picker = await _create_layer(authenticated_context, scene, collection, activeFilterBys=[0])
    assert without_a_picker.errors
    assert "publishes 0 filter(s)" in str(without_a_picker.errors[0])

    repeated = await _create_layer(authenticated_context, scene, collection, filterBys=[rule], activeFilterBys=[0, 0])
    assert repeated.errors
    assert "names the same filter twice" in str(repeated.errors[0])

    # This column is JSON, so a negative index is not even caught by the database on the way in:
    # it would sit there and apply `filterBys[-1]`, a rule the caller never selected.
    from_the_end = await _create_layer(authenticated_context, scene, collection, filterBys=[rule], activeFilterBys=[-1])
    assert from_the_end.errors
    assert "counts from 0" in str(from_the_end.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_replacing_the_filters_drops_the_applied_ones_that_are_gone(authenticated_context: HttpContext):
    """The colour picker's fallback, widened: the surviving indices stay on, the vanished ones go.

    A patch cannot say "and switch that one off", so a rule the layer no longer publishes
    simply stops being applied rather than dangling.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        filterBys=[
            {"table": table, "column": "volume", "min": 100.0, "label": "Large"},
            {"table": table, "column": "cell_type", "values": ["nucleus"], "label": "Nuclei"},
        ],
        activeFilterBys=[0, 1],
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "filterBys": [{"table": table, "column": "volume", "min": 100.0, "label": "Large"}]}},
    )
    assert not result.errors, result.errors

    updated = result.data["updateMeshLayer"]
    assert [entry["label"] for entry in updated["filterBys"]] == ["Large"]
    assert updated["activeFilterBys"] == [0], "the surviving rule is still applied; the one that is gone is not"

    cleared = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createMeshLayer"]["id"], "filterBys": []}},
    )
    assert not cleared.errors, cleared.errors
    assert cleared.data["updateMeshLayer"]["filterBys"] == []
    assert cleared.data["updateMeshLayer"]["activeFilterBys"] == [], "nothing is published, so nothing is applied and everything draws"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_both_pickers_are_checked_against_one_walk_of_the_field_edges(authenticated_context: HttpContext):
    """Two questions about one relation, so the coordinate graph is walked once, not per entry."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    # Patched on the *module*, because `core/mutations/layer.py` imports the module rather than
    # the name -- rebinding `layer.field_reachable_tables` would miss the call this asserts on.
    calls: list[object] = []
    original = attribute_plans_logic.field_reachable_tables

    def counted(*args: object, **kwargs: object):
        calls.append(args)
        return original(*args, **kwargs)

    attribute_plans_logic.field_reachable_tables = counted
    try:
        result = await _create_layer(
            authenticated_context,
            scene,
            collection,
            colorBys=[
                {"table": table, "column": "volume", "colormap": "VIRIDIS"},
                {"table": table, "column": "cell_type", "colormap": "HUES"},
            ],
            filterBys=[
                {"table": table, "column": "volume", "min": 100.0},
                {"table": table, "column": "cell_type", "values": ["nucleus"]},
            ],
        )
    finally:
        attribute_plans_logic.field_reachable_tables = original

    assert not result.errors, result.errors
    assert len(calls) == 1, f"four entries across two pickers, one walk -- got {len(calls)}"


TRACK_COLUMNS = [
    {"name": "track_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "mean_velocity", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]


async def _keyed_with_tracks(ctx: HttpContext, collection: models.MeshCollection) -> tuple[str, str]:
    """A per-object table keyed off the collection, whose `instance_id` references a tracks table."""
    tracks = await _table(ctx, "tracks", TRACK_COLUMNS)
    objects = await _table(
        ctx,
        "shape-stats",
        SHAPE_COLUMNS + [{"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks}],
        keyed_by=[{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}],
    )
    return objects, tracks


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colouring_can_live_one_table_further(authenticated_context: HttpContext):
    """The case a (table, column) pair could not express: a column of the table the ids point *at*.

    Segmentation writes the per-object table; tracking writes `tracks` and points `instance_id`
    at it. Colouring the meshes by `tracks.mean_velocity` is one lookup further, and the path is
    what makes it sayable at all.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    objects, tracks = await _keyed_with_tracks(authenticated_context, collection)

    result = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": tracks, "column": "mean_velocity", "colormap": "VIRIDIS", "label": "Velocity", "joinPath": [{"table": objects, "column": "instance_id"}]}],
        activeColorBy=0,
        filterBys=[{"table": tracks, "column": "mean_velocity", "min": 1.0, "joinPath": [{"table": objects, "column": "instance_id"}]}],
    )
    assert not result.errors, result.errors

    layer = result.data["createMeshLayer"]
    assert layer["colorBys"][0]["joinPath"] == [{"table": objects, "column": "instance_id"}]
    assert layer["colorBys"][0]["table"] == tracks
    assert layer["filterBys"][0]["joinPath"] == [{"table": objects, "column": "instance_id"}]

    stored = await models.Layer.objects.aget(id=layer["id"])
    assert stored.mesh_color_bys[0]["join_path"] == [{"table": objects, "column": "instance_id"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_join_path_is_checked_hop_by_hop(authenticated_context: HttpContext):
    """Every refusal names the hop, because "some table is unreachable" is useless in a chain."""
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    objects, tracks = await _keyed_with_tracks(authenticated_context, collection)

    # A hop through a column that identifies nothing: `volume` is a measurement, not a key.
    not_a_key = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": tracks, "column": "mean_velocity", "colormap": "VIRIDIS", "joinPath": [{"table": objects, "column": "volume"}]}],
    )
    assert not_a_key.errors
    assert "references no table" in str(not_a_key.errors[0])
    assert "joinPath[0]" in str(not_a_key.errors[0])

    # A hop that lands somewhere other than where the column says it lands.
    wrong_target = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": objects, "column": "volume", "colormap": "VIRIDIS", "joinPath": [{"table": objects, "column": "instance_id"}]}],
    )
    assert wrong_target.errors
    assert "goes where the column says it goes" in str(wrong_target.errors[0])

    # The first hop still has to start somewhere the collection's ids actually reach.
    unrelated = await _table(authenticated_context, "unrelated", SHAPE_COLUMNS)
    unreachable_root = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": tracks, "column": "mean_velocity", "colormap": "VIRIDIS", "joinPath": [{"table": unrelated, "column": "volume"}]}],
    )
    assert unreachable_root.errors
    assert "not reachable from this collection by a FIELD edge" in str(unreachable_root.errors[0])
    assert "The first hop of a `joinPath` starts there too" in str(unreachable_root.errors[0])

    # And a chain longer than the server will follow is refused rather than walked.
    too_deep = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": tracks, "column": "mean_velocity", "colormap": "VIRIDIS", "joinPath": [{"table": objects, "column": "instance_id"}] * 5}],
    )
    assert too_deep.errors
    assert "more than the 4 this server will follow" in str(too_deep.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colouring_written_before_join_paths_still_reads(authenticated_context: HttpContext):
    """The claim that let this ship without a migration, checked rather than assumed.

    The JSON columns hold pydantic dumps, so a row written before `join_path` existed has no such
    key. It must rehydrate as the direct case -- which is what it always meant -- not as an error.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})

    legacy = await sync_to_async(models.Layer.objects.create)(
        kind=enums.LayerKindChoices.MESH.value,
        scene=scene,
        mesh_collection=collection,
        mesh_color_bys=[{"table": table, "column": "volume", "colormap": "viridis", "label": "Volume"}],
        active_color_by=0,
        mesh_filter_bys=[{"table": table, "column": "volume", "min": 1.0, "max": None, "values": None, "exclude": False, "label": "Big"}],
        active_filter_bys=[0],
    )

    result = await schema.execute(
        "query Layer($id: ID!) { layer(id: $id) { ... on MeshLayer { colorBys { column joinPath { table column } } filterBys { column joinPath { table column } } } } }",
        context_value=authenticated_context,
        variable_values={"id": str(legacy.pk)},
    )
    assert not result.errors, result.errors

    layer = result.data["layer"]
    assert layer["colorBys"] == [{"column": "volume", "joinPath": []}]
    assert layer["filterBys"] == [{"column": "volume", "joinPath": []}]


DELETE_TABLE = """
mutation Delete($input: DeleteTableDatasetInput!) {
  deleteTableDataset(input: $input)
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_a_mesh_picker_names_cannot_be_deleted(authenticated_context: HttpContext):
    """The same PROTECT, over the columns a mesh layer stores its pickers in.

    Worth its own test rather than trusting the label one: a mesh picker lives in a JSON column
    of its own while a label picker lives under a key inside `label_render`, so the containment
    query reaches them by two different routes and only one of them was proven.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    keyed = [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]
    coloured = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, keyed_by=keyed)
    ruled = await _table(authenticated_context, "shape-rules", SHAPE_COLUMNS, keyed_by=keyed)

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": coloured, "column": "volume", "colormap": "VIRIDIS"}],
        activeColorBy=0,
        filterBys=[{"table": ruled, "column": "volume", "min": 1.0}],
    )
    assert not created.errors, created.errors

    for table_id, picker in ((coloured, "colour"), (ruled, "filter")):
        refused = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": table_id}})
        assert refused.errors, f"expected the table the {picker} picker names to be protected"
        assert "cannot be deleted" in str(refused.errors[0])
        assert await models.TableDataset.objects.filter(id=table_id).aexists()

    # An entry that is merely *published* still counts: `activeFilterBys` is empty here, so the
    # rule is offered and not applied -- and a picker offering a table that is gone is the same
    # broken menu whether or not anyone had switched it on.
    assert created.data["createMeshLayer"]["activeFilterBys"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_update_checks_reachability_as_hard_as_a_create(authenticated_context: HttpContext):
    """A patch is a write, so it gets the write path's checks -- both pickers, both directions.

    The easy bug here is an update that trusts what it is handed because *something* on the
    layer was validated once. And because a mesh update writes the two pickers one after the
    other, a refusal on the second must not leave the first already rewritten -- the layer is
    saved once, at the end, or not at all.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)
    table = await _table(authenticated_context, "shape-stats", SHAPE_COLUMNS, identified_by={"object": [{"kind": "MESH_COLLECTION", "meshCollection": str(collection.pk)}]})
    unrelated = await _table(authenticated_context, "unrelated", SHAPE_COLUMNS)

    created = await _create_layer(
        authenticated_context,
        scene,
        collection,
        colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "label": "Volume"}],
        activeColorBy=0,
        filterBys=[{"table": table, "column": "volume", "min": 1.0, "label": "Big"}],
        activeFilterBys=[0],
    )
    assert not created.errors, created.errors
    layer_id = created.data["createMeshLayer"]["id"]
    stored = await models.Layer.objects.aget(id=layer_id)
    before = (stored.mesh_color_bys, stored.mesh_filter_bys)

    async def patch(**fields: object):
        return await schema.execute(
            UPDATE_LAYER,
            context_value=authenticated_context,
            variable_values={"input": {"id": layer_id, **fields}},
        )

    unreachable_colour = await patch(colorBys=[{"table": unrelated, "column": "volume", "colormap": "VIRIDIS"}])
    assert unreachable_colour.errors, "expected an update naming an unreachable table to be refused"
    assert "colorBys[0]:" in str(unreachable_colour.errors[0])
    assert "not reachable from this collection by a FIELD edge" in str(unreachable_colour.errors[0])

    unreachable_rule = await patch(filterBys=[{"table": unrelated, "column": "volume", "min": 1.0}])
    assert unreachable_rule.errors, "expected the filter picker to be checked on update too"
    assert "filterBys[0]:" in str(unreachable_rule.errors[0])

    # A good colouring and a bad rule in one call: the write is all-or-nothing.
    half_good = await patch(
        colorBys=[{"table": table, "column": "cell_type", "colormap": "HUES"}],
        filterBys=[{"table": unrelated, "column": "volume", "min": 1.0}],
    )
    assert half_good.errors

    unknown_column = await patch(colorBys=[{"table": table, "column": "nope", "colormap": "VIRIDIS"}])
    assert unknown_column.errors
    assert "declares no column 'nope'" in str(unknown_column.errors[0])

    bad_hop = await patch(colorBys=[{"table": table, "column": "volume", "colormap": "VIRIDIS", "joinPath": [{"table": table, "column": "volume"}]}])
    assert bad_hop.errors
    assert "references no table" in str(bad_hop.errors[0])

    after = await models.Layer.objects.aget(id=layer_id)
    assert (after.mesh_color_bys, after.mesh_filter_bys) == before, "every refusal left both pickers exactly as they were"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_updating_a_layer_that_is_not_a_mesh_is_refused(authenticated_context: HttpContext):
    """The vocabulary is per-kind: an image layer has no material to set."""
    scene = await seed.create_scene(authenticated_context, "Composition")
    layer = await sync_to_async(models.Layer.objects.create)(kind=enums.LayerKindChoices.IMAGE.value, scene=scene)

    result = await schema.execute(
        UPDATE_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": str(layer.pk), "wireframe": True}},
    )
    assert result.errors
    assert "not a mesh layer" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_max_level_can_be_removed(authenticated_context: HttpContext):
    """The field whose own description asked for this convention.

    It read: "Raising or lowering a cap works; **removing** one does not, because a patch reads
    an omitted field and an explicit null the same way ... this one wants an UNSET convention,
    and one invented here for a single field would be worse than the limitation." This is that
    convention, so the limitation is gone.
    """
    collection = await _collection(authenticated_context)
    scene = await _scene_for(authenticated_context, collection)

    created = await _create_layer(authenticated_context, scene, collection, maxLevel=1)
    assert not created.errors, created.errors
    layer_id = created.data["createMeshLayer"]["id"]

    removed = await schema.execute(
        """
        mutation Update($input: UpdateMeshLayerInput!) {
          updateMeshLayer(input: $input) { id maxLevel }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "maxLevel": None}},
    )
    assert not removed.errors, removed.errors
    assert removed.data["updateMeshLayer"]["maxLevel"] is None

    # And omitting it still keeps the cap, which is the half that must not regress.
    restored = await schema.execute(
        """
        mutation Update($input: UpdateMeshLayerInput!) {
          updateMeshLayer(input: $input) { id maxLevel wireframe }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "maxLevel": 1}},
    )
    assert restored.data["updateMeshLayer"]["maxLevel"] == 1
    kept = await schema.execute(
        """
        mutation Update($input: UpdateMeshLayerInput!) {
          updateMeshLayer(input: $input) { id maxLevel wireframe }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "wireframe": True}},
    )
    assert kept.data["updateMeshLayer"]["maxLevel"] == 1, "an omitted field keeps its value"
