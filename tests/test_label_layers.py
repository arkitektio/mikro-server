"""A label layer is its own kind, and its pickers are the joins it exists to reach.

Three things are being pinned here. First, that `LayerKind.LABEL` is a real kind and not a
render treatment: the layer resolves as `LabelLayer` through the `Layer` interface, carries
a label recipe instead of a render graph, and none of an image's vocabulary is reachable on
it. Second, that every `colorBys` and `filterBys` entry is checked against the coordinate
graph rather than taken on faith -- the table must be one this mask's pixels actually
dereference into (a `FIELD` edge, the same relation `attributePlans` publishes), the column
must exist on it, and the way the column becomes colour (or becomes a rule) must match the
role the table declared for it. Third, that what is published is a *picker*: the author
lists the readings worth switching between and `activeColorBy` / `activeFilterBys` say which
of them are showing, exactly as a mesh layer's do.

The refusals are the interesting half. A `colorBy` naming an unrelated table is not a
display preference a client can hold until the edge shows up; it is a join nothing can
execute, and accepting it would store a layer that renders differently from what it says.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

CREATE_LABEL_LAYER = """
mutation Create($input: CreateLabelLayerInput!) {
  createLabelLayer(input: $input) {
    id
    kind
    labelRender {
      intensityAxis
      seed
      background
      contour
      selected
      showUnselected
      colorBys { table column colormap label joinPath { table column } }
      activeColorBy
      filterBys { table column min max values exclude label }
      activeFilterBys
    }
  }
}
"""

UPDATE_LABEL_LAYER = """
mutation Update($input: UpdateLabelLayerInput!) {
  updateLabelLayer(input: $input) {
    id
    opacity
    labelRender { seed contour contourWidth selected showUnselected colorBys { table column label } activeColorBy filterBys { column min max label } activeFilterBys }
  }
}
"""

SCENE_LAYERS = """
query SceneLayers($id: ID!) {
  scene(id: $id) {
    layers {
      __typename
      id
      ... on LabelLayer { labelRender { background } levelPaths { dataArray { id } } }
    }
  }
}
"""

#: A mask and its object table share `t`; the mask's `(y, x)` are consumed to produce `i`.
TYX_AXES = [
    seed.axis("t", enums.AxisType.TIME),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

OBJECT_COLUMNS = [
    {"name": "t", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "TIME"},
    {"name": "i", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
    {"name": "cell_type", "dtype": "VARCHAR", "role": "LABEL"},
]


async def _parquet(ctx: HttpContext, key: str, columns: list[tuple[str, str]] | None = None) -> models.ParquetStore:
    """A finished store carrying the file's own schema.

    The schema is load-bearing since 3b: `createTableDataset` reads a column's name and type
    off the store rather than from the caller, so a store that records none has nothing for a
    table to be declared over -- and `_resolve_store` refuses it rather than reaching for an S3
    no unit test has.
    """
    return await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization,
        populated=True, columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in (columns or [])],
    )


async def _mask_scene_lens(ctx: HttpContext, name: str = "nuclei labels") -> tuple[models.ArrayDataset, models.Scene, models.Lens]:
    """A placed mask: the dataset, a scene its intrinsic system is registered into, and a lens."""
    dataset = await seed.create_array_dataset(ctx, name, axes=TYX_AXES, shapes=[[10, 64, 64]])
    lens = await seed.create_lens(ctx, dataset)
    scene = await seed.create_scene(ctx, f"{name} scene")
    await seed.register_into_scene(ctx, scene, dataset)
    return dataset, scene, lens


async def _object_table(ctx: HttpContext, mask: models.ArrayDataset, name: str = "nuclei morphology", *, keyed: bool = True) -> str:
    """A per-object table, keyed off the mask (or deliberately not, for the refusal)."""
    variables = {
        "input": {
            "name": name,
            "data": str((await _parquet(ctx, name.replace(" ", "-"), seed.split_declaration(OBJECT_COLUMNS)[0])).pk),
            **seed.split_payload(
                OBJECT_COLUMNS,
                keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}] if keyed else None,
            ),
        }
    }
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values=variables,
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_color_by_dereferences_the_field_edge(authenticated_context: HttpContext):
    """The capability the kind exists for: colour instances by a column of the table they key into.

    Nothing about the mask says which object is which type -- the pixels hold ids. The
    answer is a row in the table the `FIELD` edge lands on, and `colorBy` is the layer
    saying which column of it to read. The edge was authored by `keyedBy`; the layer only
    points at it.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"}], "activeColorBy": 0},
            }
        },
    )
    assert not result.errors, result.errors
    data = result.data["createLabelLayer"]
    assert data["kind"] == "LABEL"
    (color_by,) = data["labelRender"]["colorBys"]
    assert color_by["table"] == table
    assert color_by["column"] == "cell_type"
    assert color_by["colormap"] == "HUES", "a categorical column takes a qualitative colormap -- a colour per distinct value"
    assert color_by["label"] == "Cell type", "the caption the picker row shows"
    assert color_by["joinPath"] == [], "the direct case: the mask's ids key this table"
    assert data["labelRender"]["activeColorBy"] == 0, "publishing one colouring and drawing it are two statements"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_measure_column_takes_a_colormap(authenticated_context: HttpContext):
    """The other half of the same rule: a measured column is coloured over its range."""
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS"}], "activeColorBy": 0},
            }
        },
    )
    assert not result.errors, result.errors
    (color_by,) = result.data["createLabelLayer"]["labelRender"]["colorBys"]
    assert color_by["column"] == "area"
    assert color_by["colormap"] == "VIRIDIS"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_color_by_refuses_a_table_no_field_edge_reaches(authenticated_context: HttpContext):
    """A table the mask does not key into is a join nothing can execute.

    The table is real and the column exists; what is missing is the one thing that makes
    the question answerable -- a `FIELD` edge from the mask's pixels to its rows. Without
    it there is no way to get from a pixel value to a row, so the layer would name a colour
    rule no renderer could follow.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    unrelated = await _object_table(authenticated_context, mask, name="unrelated objects", keyed=False)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": unrelated, "column": "area", "colormap": "VIRIDIS"}]},
            }
        },
    )
    assert result.errors, "expected a colorBy naming an unkeyed table to be refused"
    assert "colorBys[0]:" in str(result.errors[0]), "the entry's index rides in the refusal"
    assert "not reachable from this mask by a FIELD edge" in str(result.errors[0])
    assert await models.Layer.objects.filter(scene_id=scene.id).acount() == 0, "the refusal left no layer behind"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_color_by_refuses_a_colormap_over_a_categorical_column(authenticated_context: HttpContext):
    """A *continuous* colormap over a class column would impose an order the values do not have.

    A qualitative one does not, which is the whole reason the enum has both sorts, so the
    refusal names the sort rather than the field.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": table, "column": "cell_type", "colormap": "VIRIDIS"}]},
            }
        },
    )
    assert result.errors, "expected a continuous colormap over a LABEL column to be refused"
    assert "categorical" in str(result.errors[0]) and "qualitative" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_color_by_refuses_a_qualitative_colormap_over_a_measure_column(authenticated_context: HttpContext):
    """And the mirror: a palette over a continuous measurement throws its order away."""
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": table, "column": "area", "colormap": "HUES"}]},
            }
        },
    )
    assert result.errors, "expected a qualitative colormap over an ATTRIBUTE column to be refused"
    assert "measured" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_color_by_refuses_an_undeclared_column(authenticated_context: HttpContext):
    """The edge is right and the column is not: the table's own schema settles it."""
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {"colorBys": [{"table": table, "column": "perimeter", "colormap": "VIRIDIS"}]},
            }
        },
    )
    assert result.errors, "expected a colorBy naming an undeclared column to be refused"
    assert "declares no column 'perimeter'" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_is_a_patch_not_a_replacement(authenticated_context: HttpContext):
    """Toggling `contour` must not drop the selection the client is not sending.

    A label layer's settings are edited one at a time from a viewer -- a contour toggle
    here, a click on an object there. If an omitted field reset to its default, every one
    of those edits would silently discard the rest of the state.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "render": {
                    "selected": [7, 42],
                    "showUnselected": False,
                    "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS"}],
                    "activeColorBy": 0,
                    "filterBys": [{"table": table, "column": "area", "min": 100.0, "label": "Large"}],
                    "activeFilterBys": [0],
                },
            }
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]

    updated = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"contour": True, "contourWidth": 2.0}, "opacity": 0.5}},
    )
    assert not updated.errors, updated.errors
    render = updated.data["updateLabelLayer"]["labelRender"]
    assert render["contour"] is True and render["contourWidth"] == 2.0, "what was sent changed"
    assert render["selected"] == [7, 42], "what was not sent survived"
    assert render["showUnselected"] is False
    assert [entry["column"] for entry in render["colorBys"]] == ["area"], "the picker the client is not sending survived"
    assert render["activeColorBy"] == 0
    assert [entry["column"] for entry in render["filterBys"]] == ["area"]
    assert render["activeFilterBys"] == [0]
    assert updated.data["updateLabelLayer"]["opacity"] == 0.5


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_label_layer_refuses_an_image_layer(authenticated_context: HttpContext):
    """The two kinds' render settings are different vocabularies, so their updates are too."""
    _, scene, lens = await _mask_scene_lens(authenticated_context)

    created = await schema.execute(
        "mutation Create($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createIntensityLayer"]["id"], "render": {"contour": True}}},
    )
    assert result.errors, "expected updateLabelLayer over an image layer to be refused"
    assert "not a label layer" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_layer_refuses_a_label_layer(authenticated_context: HttpContext):
    """The mirror guard, and the one that protects the split.

    Without it a single `updateLayer` writes a render graph onto a label layer and leaves
    it carrying both recipes -- the two-schemas-in-one-place state the separate
    `label_render` column exists to make unrepresentable.
    """
    _, scene, lens = await _mask_scene_lens(authenticated_context)

    created = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]

    result = await schema.execute(
        "mutation U($input: UpdateLayerInput!) { updateLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {
                "id": layer_id,
                "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel", "transfer": {"colormap": "VIRIDIS"}}]}},
            }
        },
    )
    assert result.errors, "expected updateLayer over a label layer to be refused"
    assert "not its render vocabulary" in str(result.errors[0])

    layer = await models.Layer.objects.aget(id=layer_id)
    assert layer.render_graph is None and layer.label_render is not None, "the refusal left one recipe, not two"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_label_layer_resolves_through_the_scene_interface(authenticated_context: HttpContext):
    """`Scene.layers` returns it as a `LabelLayer`, and it places like any lens-backed layer.

    This is what the `layer_types` registration buys: a subtype reachable only through the
    `Layer` interface is dropped from the SDL unless it is listed there, and the failure is
    a resolution error at query time rather than anything a create test would notice.
    `levelPaths` is here for the second half -- a label map has a pyramid, and placement is
    a fact of its lens' space, which it shares with an image layer.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)

    created = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(SCENE_LAYERS, context_value=authenticated_context, variable_values={"id": str(scene.id)})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    assert layer["__typename"] == "LabelLayer"
    assert layer["labelRender"]["background"] == 0
    assert len(layer["levelPaths"]) == 1, "one pyramid level, placed like any lens-backed layer"


# ---------------------------------------------------------------------------
# The pickers
#
# A label layer publishes an ordered list of colourings and an ordered list of filters, and
# stores which of them are showing as indices into those lists. The author decides what is
# worth switching between; the person at the screen decides which one they are looking at.
# Everything below is about that split, and about the two ways it can be written wrong: an
# index that points at nothing, and a picker whose entries a viewer cannot tell apart.
# ---------------------------------------------------------------------------

LABEL_OPTIONS = """
query Options($lens: ID!, $filters: ColumnOptionFilter, $maxJoinDepth: Int) {
  labelColorByOptions(lens: $lens, filters: $filters, maxJoinDepth: $maxJoinDepth) {
    control
    column { name role }
    table { id name }
    joinPath { table { id } column { name } }
  }
  labelFilterByOptions(lens: $lens, filters: $filters, maxJoinDepth: $maxJoinDepth) {
    column { name }
    table { id }
    joinPath { column { name } }
  }
}
"""

TRACK_COLUMNS = [
    {"name": "track_id", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"},
    {"name": "mean_velocity", "dtype": "DOUBLE", "role": "ATTRIBUTE", "unit": "micrometer / second"},
]


async def _tracks_table(ctx: HttpContext, name: str = "nuclei tracks") -> str:
    """A table nothing keys off, reachable only by a `references` hop out of the object table."""
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"name": name, "data": str((await _parquet(ctx, name.replace(" ", "-"), seed.split_declaration(TRACK_COLUMNS)[0])).pk), **seed.split_payload(TRACK_COLUMNS)}},
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _hopping_object_table(ctx: HttpContext, mask: models.ArrayDataset, tracks: str, name: str = "nuclei morphology") -> str:
    """The per-object table, with a column whose values identify rows of `tracks`."""
    columns = [*OBJECT_COLUMNS, {"name": "instance_id", "dtype": "BIGINT", "role": "TRACK_ID", "references": tracks}]
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "name": name,
                "data": str((await _parquet(ctx, name.replace(" ", "-"), seed.split_declaration(columns)[0])).pk),
                **seed.split_payload(columns, keyed_by=[{"kind": "DATASET", "dataset": str(mask.pk)}]),
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _create(ctx: HttpContext, scene, lens, render: dict):
    return await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "render": render}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_picker_publishes_several_colourings_and_draws_one(authenticated_context: HttpContext):
    """The whole point of a list: two honest readings of one mask, and one of them showing.

    Area through a colormap and cell type through class colours are not two attempts at the
    same thing -- they answer different questions about the same objects. Which one a viewer
    wants is theirs to decide, so both are published and the index says where the viewer is.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"},
                {"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"},
            ],
            "activeColorBy": 1,
        },
    )
    assert not result.errors, result.errors
    render = result.data["createLabelLayer"]["labelRender"]
    assert [entry["label"] for entry in render["colorBys"]] == ["Area", "Cell type"], "the order published is the order a menu shows"
    assert render["activeColorBy"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_deprecated_color_by_reads_the_active_entry(authenticated_context: HttpContext):
    """One copy of the choice, and it is the index -- the old field is a view onto it.

    Kept because clients selecting `colorBy` predate the picker. It is derived on every read,
    so it cannot drift from `colorBys`/`activeColorBy` the way a stored duplicate could.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    query = 'mutation C($input: CreateLabelLayerInput!) { createLabelLayer(input: $input) { labelRender { colorBy { column } } } }'
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "render": {
                "colorBys": [
                    {"table": table, "column": "area", "colormap": "VIRIDIS"},
                    {"table": table, "column": "cell_type", "colormap": "HUES"},
                ],
                "activeColorBy": 1,
            },
        }
    }
    result = await schema.execute(query, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors
    assert result.data["createLabelLayer"]["labelRender"]["colorBy"]["column"] == "cell_type"

    variables["input"]["render"].pop("activeColorBy")
    result = await schema.execute(query, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors
    assert result.data["createLabelLayer"]["labelRender"]["colorBy"] is None, "a published picker nobody has chosen from draws the hash"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_active_index_that_points_at_nothing_is_refused(authenticated_context: HttpContext):
    """Past the end, and below the beginning.

    A negative index is the dangerous one: `colorBys[-1]` is valid Python and a valid `Int`, so
    nothing downstream would ever notice -- the viewer would simply get someone else's colouring.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)
    entry = {"table": table, "column": "area", "colormap": "VIRIDIS"}

    past_end = await _create(authenticated_context, scene, lens, {"colorBys": [entry], "activeColorBy": 1})
    assert past_end.errors, "expected an index past the last entry to be refused"
    assert "indexed 0..0" in str(past_end.errors[0])

    negative = await _create(authenticated_context, scene, lens, {"colorBys": [entry], "activeColorBy": -1})
    assert negative.errors, "expected a negative index to be refused"
    assert "counts from 0" in str(negative.errors[0])

    empty = await _create(authenticated_context, scene, lens, {"activeColorBy": 0})
    assert empty.errors, "expected an index into an empty picker to be refused"
    assert "hash each id to a colour" in str(empty.errors[0]), "the refusal names what a null index means on a mask, not on a mesh"

    assert await models.Layer.objects.filter(scene_id=scene.id).acount() == 0, "no refusal left a layer behind"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_entries_that_render_identically_are_refused(authenticated_context: HttpContext):
    """A picker whose two rows draw the same thing asks a viewer to choose between a thing and itself.

    The caption is deliberately not part of what makes an entry distinct -- a second name is not
    a second colouring. Two *colormaps* over one column stay legal: those genuinely differ.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    duplicate = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"},
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Size"},
            ]
        },
    )
    assert duplicate.errors, "expected two identically-rendering entries to be refused"
    assert "one colouring wearing two names" in str(duplicate.errors[0])

    distinct = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"},
                {"table": table, "column": "area", "colormap": "MAGMA", "label": "Area (magma)"},
            ]
        },
    )
    assert not distinct.errors, distinct.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filters_keep_and_drop_objects_by_the_same_join(authenticated_context: HttpContext):
    """The colour picker's sibling: which objects are drawn, over the same FIELD edge.

    Several rules at once is the normal case, not a contradiction -- they combine with AND --
    which is why `activeFilterBys` is a list where `activeColorBy` is one index. Two entries
    over one column are allowed here for the same reason: 'small' and 'large' are two rules.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    result = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "filterBys": [
                {"table": table, "column": "area", "min": 100.0, "label": "Large"},
                {"table": table, "column": "area", "max": 100.0, "label": "Small"},
                {"table": table, "column": "cell_type", "values": ["debris"], "exclude": True, "label": "Not debris"},
            ],
            "activeFilterBys": [0, 2],
        },
    )
    assert not result.errors, result.errors
    render = result.data["createLabelLayer"]["labelRender"]
    assert [entry["label"] for entry in render["filterBys"]] == ["Large", "Small", "Not debris"]
    assert render["filterBys"][0]["min"] == 100.0 and render["filterBys"][0]["max"] is None, "one open end is still a range"
    assert render["filterBys"][2]["values"] == ["debris"] and render["filterBys"][2]["exclude"] is True
    assert render["activeFilterBys"] == [0, 2], "two rules applied at once, ANDed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_filter_must_match_the_column_role(authenticated_context: HttpContext):
    """The same measure-vs-categorical split the colouring turns on, and the same refusals.

    A bound over a class column would impose an order the values do not have; a value list over
    a continuous measurement names points on a line. Which applies is the table's answer, given
    when the column declared its role -- not a choice made here.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    bounded_class = await _create(authenticated_context, scene, lens, {"filterBys": [{"table": table, "column": "cell_type", "min": 1.0}]})
    assert bounded_class.errors, "expected a bound over a LABEL column to be refused"
    assert "filterBys[0]:" in str(bounded_class.errors[0]) and "categorical" in str(bounded_class.errors[0])

    listed_measure = await _create(authenticated_context, scene, lens, {"filterBys": [{"table": table, "column": "area", "values": ["3.5"]}]})
    assert listed_measure.errors, "expected a values list over an ATTRIBUTE column to be refused"
    assert "measured" in str(listed_measure.errors[0])

    both = await _create(authenticated_context, scene, lens, {"filterBys": [{"table": table, "column": "area", "min": 1.0, "values": ["3.5"]}]})
    assert both.errors, "expected a rule naming both halves to be refused"

    neither = await _create(authenticated_context, scene, lens, {"filterBys": [{"table": table, "column": "area"}]})
    assert neither.errors, "expected a rule that matches everything to be refused"

    duplicate_index = await _create(
        authenticated_context,
        scene,
        lens,
        {"filterBys": [{"table": table, "column": "area", "min": 1.0}], "activeFilterBys": [0, 0]},
    )
    assert duplicate_index.errors, "expected the same rule applied twice to be refused"
    assert "narrows nothing" in str(duplicate_index.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_picker_entry_may_reach_one_table_further(authenticated_context: HttpContext):
    """`joinPath` is the hop the coordinate graph deliberately stops before.

    The mask's ids key the object table; `instance_id` *references* the tracks table, which is a
    schema fact, not an edge. The server records the chain and checks it hop by hop; the client
    still performs the lookups.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    tracks = await _tracks_table(authenticated_context)
    objects = await _hopping_object_table(authenticated_context, mask, tracks)

    result = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {
                    "table": tracks,
                    "column": "mean_velocity",
                    "colormap": "VIRIDIS",
                    "label": "Track speed",
                    "joinPath": [{"table": objects, "column": "instance_id"}],
                }
            ],
            "activeColorBy": 0,
        },
    )
    assert not result.errors, result.errors
    (entry,) = result.data["createLabelLayer"]["labelRender"]["colorBys"]
    assert entry["table"] == tracks
    assert entry["joinPath"] == [{"table": objects, "column": "instance_id"}], "stored as the server resolved it"

    # And the hop is checked: a path whose column identifies rows of something else is refused.
    wrong = await _create(
        authenticated_context,
        scene,
        lens,
        {"colorBys": [{"table": tracks, "column": "mean_velocity", "colormap": "VIRIDIS", "joinPath": [{"table": objects, "column": "area"}]}]},
    )
    assert wrong.errors, "expected a hop through a column that references nothing to be refused"
    assert "references no table" in str(wrong.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_update_replaces_a_picker_wholesale_and_can_clear_it(authenticated_context: HttpContext):
    """The order *is* the identity, so there is nothing to merge on -- and `[]` finally means 'none'.

    This is what a picker buys that a single `colorBy` could not: a patch reads an omitted field
    and an explicit null the same way, so a colouring used to be unremovable. An empty list is a
    value, and it says what null could not.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"}],
            "activeColorBy": 0,
            "filterBys": [{"table": table, "column": "area", "min": 100.0, "label": "Large"}],
            "activeFilterBys": [0],
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]

    replaced = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "id": layer_id,
                "render": {"colorBys": [{"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"}], "activeColorBy": 0},
            }
        },
    )
    assert not replaced.errors, replaced.errors
    render = replaced.data["updateLabelLayer"]["labelRender"]
    assert [entry["label"] for entry in render["colorBys"]] == ["Cell type"], "replaced, never merged"
    assert [entry["label"] for entry in render["filterBys"]] == ["Large"], "the picker nobody named is untouched"

    cleared = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"colorBys": [], "filterBys": []}}},
    )
    assert not cleared.errors, cleared.errors
    render = cleared.data["updateLabelLayer"]["labelRender"]
    assert render["colorBys"] == [] and render["filterBys"] == []
    assert render["activeColorBy"] is None, "nothing left to draw but the hash"
    assert render["activeFilterBys"] == [], "and nothing left to apply"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_switching_the_active_entry_needs_nothing_but_the_index(authenticated_context: HttpContext):
    """The click-a-picker-row path, which is the one a viewer walks constantly.

    Naming an index and nothing else must not require resending the picker -- and must not walk
    the coordinate graph either, because there is no new entry to check: the index is validated
    against what is already stored. The published lists come back untouched, which is the whole
    point of storing the choice as an index rather than as a copy of the chosen entry.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"},
                {"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"},
            ],
            "activeColorBy": 0,
            "filterBys": [
                {"table": table, "column": "area", "min": 100.0, "label": "Large"},
                {"table": table, "column": "area", "max": 100.0, "label": "Small"},
            ],
            "activeFilterBys": [0],
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]

    switched = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"activeColorBy": 1, "activeFilterBys": [1]}}},
    )
    assert not switched.errors, switched.errors
    render = switched.data["updateLabelLayer"]["labelRender"]
    assert render["activeColorBy"] == 1 and render["activeFilterBys"] == [1], "the switch took"
    assert [entry["label"] for entry in render["colorBys"]] == ["Area", "Cell type"], "the picker nobody resent is intact"
    assert [entry["label"] for entry in render["filterBys"]] == ["Large", "Small"]

    # And an index alone is still checked -- against the stored picker, since none was sent.
    dangling = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"activeColorBy": 5}}},
    )
    assert dangling.errors, "expected an index past the stored picker to be refused"
    assert "indexed 0..1" in str(dangling.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shortened_picker_drops_the_indices_it_no_longer_holds(authenticated_context: HttpContext):
    """A patch cannot say 'and switch that one off', so an unpublished entry stops being shown.

    The indices that survive keep pointing at what they pointed at, because the list is replaced
    wholesale and never reordered under a caller -- which is what makes dropping the rest safe.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [
                {"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"},
                {"table": table, "column": "cell_type", "colormap": "HUES", "label": "Cell type"},
            ],
            "activeColorBy": 1,
            "filterBys": [
                {"table": table, "column": "area", "min": 100.0, "label": "Large"},
                {"table": table, "column": "area", "max": 100.0, "label": "Small"},
            ],
            "activeFilterBys": [0, 1],
        },
    )
    assert not created.errors, created.errors

    shortened = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "id": created.data["createLabelLayer"]["id"],
                "render": {
                    "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"}],
                    "filterBys": [{"table": table, "column": "area", "min": 100.0, "label": "Large"}],
                },
            }
        },
    )
    assert not shortened.errors, shortened.errors
    render = shortened.data["updateLabelLayer"]["labelRender"]
    assert render["activeColorBy"] is None, "the entry that was drawn is no longer published"
    assert render["activeFilterBys"] == [0], "the rule that survived is still applied; the one that did not is gone"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_options_offered_are_the_options_accepted(authenticated_context: HttpContext):
    """The invariant a lens-rooted options query exists for, asserted by writing what it returns.

    A picker built on a set that merely overlaps the write path's either hides legal choices or
    proposes refusals. Both queries return the same candidates on purpose: a colouring and a rule
    reach the same column through the same join and branch on the same split.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    tracks = await _tracks_table(authenticated_context)
    objects = await _hopping_object_table(authenticated_context, mask, tracks)
    await _object_table(authenticated_context, mask, name="unrelated objects", keyed=False)

    result = await schema.execute(LABEL_OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id), "maxJoinDepth": 1})
    assert not result.errors, result.errors
    options = result.data["labelColorByOptions"]

    direct = {option["column"]["name"] for option in options if not option["joinPath"]}
    assert direct == {"t", "i", "area", "cell_type", "instance_id"}, "every column of the keyed table, and nothing from the table no edge reaches"
    hopped = [option for option in options if option["joinPath"]]
    assert {option["column"]["name"] for option in hopped} == {"track_id", "mean_velocity"}
    assert next(option for option in hopped if option["column"]["name"] == "mean_velocity")["joinPath"] == [
        {"table": {"id": objects}, "column": {"name": "instance_id"}}
    ]

    assert [(option["table"]["id"], option["column"]["name"]) for option in result.data["labelFilterByOptions"]] == [
        (option["table"]["id"], option["column"]["name"]) for option in options
    ], "one relation, one walk, two names"

    # Written back verbatim, every one of them is accepted -- which is the whole claim.
    for option in options:
        entry = {
            "table": option["table"]["id"],
            "column": option["column"]["name"],
            "joinPath": [{"table": step["table"]["id"], "column": step["column"]["name"]} for step in option["joinPath"]],
        }
        entry.update({"colormap": "VIRIDIS"} if option["control"] == "MEASURE" else {"colormap": "HUES"})
        written = await _create(authenticated_context, scene, lens, {"colorBys": [entry]})
        assert not written.errors, f"{option['column']['name']} was offered and refused: {written.errors}"

    narrowed = await schema.execute(
        LABEL_OPTIONS,
        context_value=authenticated_context,
        variable_values={"lens": str(lens.id), "maxJoinDepth": 1, "filters": {"directOnly": True, "controls": ["CATEGORICAL"]}},
    )
    assert not narrowed.errors, narrowed.errors
    assert {option["column"]["name"] for option in narrowed.data["labelColorByOptions"]} == {"cell_type", "instance_id"}


DELETE_TABLE = """
mutation Delete($input: DeleteTableDatasetInput!) {
  deleteTableDataset(input: $input)
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_a_picker_names_cannot_be_deleted(authenticated_context: HttpContext):
    """PROTECT, spelled out in a guard because the reference lives in JSON and cascades nowhere.

    The boundary refuses a picker naming an unreachable table; deleting the table afterwards
    would arrive at exactly that state by the back door, and the layer would look valid until a
    renderer tried the join. Refusing the delete puts the discovery back where the decision is.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)
    spare = await _object_table(authenticated_context, mask, name="unused objects")

    created = await _create(authenticated_context, scene, lens, {"colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS"}], "activeColorBy": 0})
    assert not created.errors, created.errors

    refused = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": table}})
    assert refused.errors, "expected a table a picker colours by to be protected"
    message = str(refused.errors[0])
    assert "cannot be deleted" in message
    assert f"scene '{scene.name}'" in message, "name what is holding it, or the caller has nowhere to go"
    assert await models.TableDataset.objects.filter(id=table).aexists(), "the refusal left the table alone"

    # A table nothing names is still deletable: the guard protects, it does not freeze.
    freed = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": spare}})
    assert not freed.errors, freed.errors

    # Clearing the picker releases it -- the way out the refusal names.
    layer_id = created.data["createLabelLayer"]["id"]
    cleared = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"colorBys": []}}},
    )
    assert not cleared.errors, cleared.errors

    now_allowed = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": table}})
    assert not now_allowed.errors, now_allowed.errors


DELETE_EDGE = """
mutation Delete($input: DeleteTransformationInput!) {
  deleteTransformation(input: $input)
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_field_edge_a_picker_reaches_through_is_protected(authenticated_context: HttpContext):
    """The second route to a stranded picker: keep the table, remove the crossing.

    And the reason the guard asks a hypothetical rather than reading the edge: a *rival* FIELD
    edge still providing the crossing means deleting this one breaks nothing, and refusing it
    would be the guard inventing a problem. RFC-9 allows those rivals, so the only honest
    question is what the walk says with this edge gone.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(authenticated_context, scene, lens, {"colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS"}], "activeColorBy": 0})
    assert not created.errors, created.errors

    def field_edges() -> list[models.Transformation]:
        return list(models.Transformation.objects.filter(kind=enums.TransformKindChoices.FIELD.value).order_by("pk"))

    edges = await sync_to_async(field_edges)()
    assert len(edges) == 1, "createTableDataset(keyedBy:) authored exactly one crossing"
    edge = edges[0]

    refused = await schema.execute(DELETE_EDGE, context_value=authenticated_context, variable_values={"input": {"id": str(edge.pk)}})
    assert refused.errors, "expected the only crossing a picker reaches through to be protected"
    message = str(refused.errors[0])
    assert "cannot be deleted" in message
    assert f"scene '{scene.name}'" in message

    # A rival crossing: now this edge is not the one holding the picker up, and deleting it
    # strands nothing. The guard must notice, which is only possible by re-walking without it.
    def author_rival() -> models.Transformation:
        rival = models.Transformation.objects.get(pk=edge.pk)
        rival.pk = None
        rival._state.adding = True
        rival.save()
        return rival

    rival = await sync_to_async(author_rival)()
    assert rival.pk != edge.pk

    allowed = await schema.execute(DELETE_EDGE, context_value=authenticated_context, variable_values={"input": {"id": str(edge.pk)}})
    assert not allowed.errors, f"a rival crossing still reaches the table, so this delete breaks nothing: {allowed.errors}"

    # With the rival now the last one, the protection is back.
    refused_again = await schema.execute(DELETE_EDGE, context_value=authenticated_context, variable_values={"input": {"id": str(rival.pk)}})
    assert refused_again.errors, "the last crossing is protected again"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_guard_finds_a_table_a_join_path_only_passes_through(authenticated_context: HttpContext):
    """The half that is easy to forget: a hop table is named nowhere but inside `joinPath`.

    Deleting a table the path merely passes through breaks the join exactly as thoroughly as
    deleting the one the value is read from, and it is invisible to a guard that only reads each
    entry's `table`.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    tracks = await _tracks_table(authenticated_context)
    objects = await _hopping_object_table(authenticated_context, mask, tracks)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "filterBys": [
                {
                    "table": tracks,
                    "column": "mean_velocity",
                    "min": 1.0,
                    "joinPath": [{"table": objects, "column": "instance_id"}],
                }
            ],
            "activeFilterBys": [0],
        },
    )
    assert not created.errors, created.errors

    # `objects` appears only as a hop -- no entry reads a value from it.
    hop_refused = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": objects}})
    assert hop_refused.errors, "expected the table the path hops through to be protected too"
    assert "cannot be deleted" in str(hop_refused.errors[0])

    terminal_refused = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": tracks}})
    assert terminal_refused.errors, "and the table the value is read from"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_update_checks_reachability_as_hard_as_a_create(authenticated_context: HttpContext):
    """A patch is a write, so it gets the write path's checks -- both pickers, both directions.

    The easy bug here is an update that trusts what it is handed because *something* on the
    layer was validated once. Every entry naming a table this mask's ids do not dereference into
    is a join nothing can execute, whether it arrives at creation or an hour later, and a
    refusal must leave the layer exactly as it was rather than half-rewritten.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)
    unrelated = await _object_table(authenticated_context, mask, name="someone else's objects", keyed=False)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"}],
            "activeColorBy": 0,
            "filterBys": [{"table": table, "column": "area", "min": 1.0, "label": "Big"}],
            "activeFilterBys": [0],
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]
    before = (await models.Layer.objects.aget(id=layer_id)).label_render

    async def patch(render: dict):
        return await schema.execute(
            UPDATE_LABEL_LAYER,
            context_value=authenticated_context,
            variable_values={"input": {"id": layer_id, "render": render}},
        )

    unreachable_colour = await patch({"colorBys": [{"table": unrelated, "column": "area", "colormap": "VIRIDIS"}]})
    assert unreachable_colour.errors, "expected an update naming an unkeyed table to be refused"
    assert "colorBys[0]:" in str(unreachable_colour.errors[0])
    assert "not reachable from this mask by a FIELD edge" in str(unreachable_colour.errors[0])

    unreachable_rule = await patch({"filterBys": [{"table": unrelated, "column": "area", "min": 1.0}]})
    assert unreachable_rule.errors, "expected the filter picker to be checked on update too"
    assert "filterBys[0]:" in str(unreachable_rule.errors[0])
    assert "not reachable from this mask by a FIELD edge" in str(unreachable_rule.errors[0])

    unknown_column = await patch({"colorBys": [{"table": table, "column": "nope", "colormap": "VIRIDIS"}]})
    assert unknown_column.errors
    assert "declares no column 'nope'" in str(unknown_column.errors[0])

    # A hop is checked on update as well: the path is part of the entry, not decoration.
    bad_hop = await patch({"colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "joinPath": [{"table": table, "column": "area"}]}]})
    assert bad_hop.errors
    assert "references no table" in str(bad_hop.errors[0])

    assert (await models.Layer.objects.aget(id=layer_id)).label_render == before, "every refusal left the stored recipe exactly as it was"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_offers_its_own_options(authenticated_context: HttpContext):
    """The nested field, over the same walk: what a mask can be coloured by, without a second round trip.

    A client looking at a lens should not have to carry its id back to a root query to learn what
    it may colour by -- the same courtesy `MeshCollection.colorByOptions` extends over a
    collection. The list must be the root query's, or the two would be two answers.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    await _object_table(authenticated_context, mask)

    nested = await schema.execute(
        "query Nested($id: ID!) { lens(id: $id) { colorByOptions { column { name } control joinPath { column { name } } } } }",
        context_value=authenticated_context,
        variable_values={"id": str(lens.id)},
    )
    assert not nested.errors, nested.errors

    flat = await schema.execute(LABEL_OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id)})
    assert not flat.errors, flat.errors

    assert [option["column"]["name"] for option in nested.data["lens"]["colorByOptions"]] == [
        option["column"]["name"] for option in flat.data["labelColorByOptions"]
    ], "one walk, one answer, two ways to ask"


# The two tests that lived here exercised `_fold_into_pickers`, the one-time data migration in
# `0004_label_pickers` that rewrote pre-picker `color_by` blobs into the `color_bys` /
# `active_color_by` shape. Both imported that migration module directly and ran it against a
# hand-built row.
#
# They are gone with it. The migration history was squashed to a single `0001_initial`, and this
# release carries no compatibility obligation to rows written before it -- so there is no
# "pre-picker shape" left for a fold to find, and no module to import. What the picker shape
# *is* stays covered by the mutation tests above, which is the half that still has a subject.


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_active_entry_can_be_cleared_without_clearing_the_picker(authenticated_context: HttpContext):
    """The case that had no spelling at all.

    "Publish these colourings but draw none of them -- hash the ids" was unreachable while
    `null` also meant "omitted": a patch could not tell the two apart, so switching a colouring
    off was only ever a side effect of shortening the list. An explicit null says it now, and
    the picker survives.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"}],
            "activeColorBy": 0,
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLabelLayer"]["id"]

    cleared = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"activeColorBy": None}}},
    )
    assert not cleared.errors, cleared.errors
    render = cleared.data["updateLabelLayer"]["labelRender"]
    assert render["activeColorBy"] is None, "an explicit null draws none of the picker"
    assert len(render["colorBys"]) == 1, "and the picker itself is untouched"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_omitted_active_entry_still_leaves_the_choice_alone(authenticated_context: HttpContext):
    """The other half of the same distinction, and the regression this could break.

    Omitting the field has always meant "keep it", and it still must -- a client toggling
    `contour` cannot silently switch the colouring off.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    created = await _create(
        authenticated_context,
        scene,
        lens,
        {
            "colorBys": [{"table": table, "column": "area", "colormap": "VIRIDIS", "label": "Area"}],
            "activeColorBy": 0,
        },
    )
    layer_id = created.data["createLabelLayer"]["id"]

    toggled = await schema.execute(
        UPDATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"contour": True}}},
    )
    assert not toggled.errors, toggled.errors
    render = toggled.data["updateLabelLayer"]["labelRender"]
    assert render["contour"] is True
    assert render["activeColorBy"] == 0, "an omitted field keeps its value"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_intensity_axis_can_be_cleared(authenticated_context: HttpContext):
    """`intensityAxis` had the same problem: set it once and it could never be unset.

    Null means "read the pixel value itself as the id", which is what a mask means, and was
    indistinguishable from "leave it alone".
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    created = await _create(authenticated_context, scene, lens, {})
    layer_id = created.data["createLabelLayer"]["id"]

    cleared = await schema.execute(
        """
        mutation Update($input: UpdateLabelLayerInput!) {
          updateLabelLayer(input: $input) { id labelRender { intensityAxis } }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": layer_id, "render": {"intensityAxis": None}}},
    )
    assert not cleared.errors, cleared.errors
    assert cleared.data["updateLabelLayer"]["labelRender"]["intensityAxis"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colouring_that_contradicts_its_kind_names_what_the_kind_reads(authenticated_context: HttpContext):
    """The union's message, and why it is better than the branches it replaced.

    `ColorByInputModel` used to hand-check which fields each kind reads in four branches, each
    naming only what the caller must NOT have sent. `parse_union_member` names what the kind
    DOES read, which is the actionable half — `core.input_unions` exists for exactly this, and
    `TransformInput` has used it all along.
    """
    mask, scene, lens = await _mask_scene_lens(authenticated_context)
    table = await _object_table(authenticated_context, mask)

    mixed = await _create(
        authenticated_context,
        scene,
        lens,
        {"colorBys": [{"table": table, "column": "area", "dataset": "1", "colormap": "VIRIDIS"}]},
    )
    assert mixed.errors
    message = str(mixed.errors[0])
    assert "does not read `dataset`" in message
    assert "it reads" in message, "the message names what the kind DOES read"

    missing = await _create(
        authenticated_context,
        scene,
        lens,
        {"colorBys": [{"kind": "SPARSE", "colormap": "MAGMA"}]},
    )
    assert missing.errors
    assert "requires `dataset`" in str(missing.errors[0])
