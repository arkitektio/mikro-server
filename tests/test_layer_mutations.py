"""Tests for the composable in-layer render graph and the layer helper mutations.

A layer is the alpha-blended unit; inside it a render graph combines one or more
channels (each with its own transfer function). These tests cover: creating a
layer with an explicit multi-channel render graph, the relaxed single-channel
rule, the round-trip of the typed render-graph output, and the three convenience
builders (createRgbLayer / createIntensityLayer / createLabelLayer).
"""

import pytest

from asgiref.sync import sync_to_async
from core import enums, models
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed


async def _seed_lens(ctx: HttpContext, *, axis_names, shape, descriptors) -> models.Lens:
    dataset = await seed.create_array_dataset(ctx, "LensDS", axes=descriptors, shapes=[shape])
    return await seed.create_lens(ctx, dataset)


async def _seed_scene(ctx: HttpContext, lens: models.Lens | None = None) -> models.Scene:
    """A scene -- and, when a lens is given, the explicit registration its dataset needs.

    Layer mutations no longer fabricate placements: an unplaced source is refused. Passing
    the lens here is the test-side equivalent of the createTransformation step a real
    client takes before composing a layer.
    """
    scene = await seed.create_scene(ctx)
    if lens is not None:
        # `lens.dataset` is cached from creation, so this reads no DB in async context.
        await seed.register_into_scene(ctx, scene, lens.dataset)
    return scene


async def _rgb_lenses(ctx: HttpContext, lens: models.Lens) -> dict[str, str]:
    """The three single-channel crops an RGB layer names, as its input fields: channels 0, 1 and 2 of ``lens``."""
    return {field: str((await seed.channel_lens(ctx, lens, index)).pk) for index, field in enumerate(("redLens", "greenLens", "blueLens"))}


# The axes are ordered channel-before-space, which is conventional rather than required:
# nothing holds a declaration to a type ordering. Within the spatial block, the last axis is x,
# and that part is read.
_CYX = (
    ["c", "y", "x"],
    [3, 32, 32],
    [
        seed.axis("c", enums.AxisType.CHANNEL),
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ],
)
_YX = (
    ["y", "x"],
    [32, 32],
    [
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ],
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_layer_with_render_graph(db, authenticated_context: HttpContext):
    """A layer may combine multiple channels via a render graph, relaxing the single-channel rule."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    red, green = await seed.channel_lens(authenticated_context, lens, 0), await seed.channel_lens(authenticated_context, lens, 1)

    mutation = """
        mutation Create($input: CreateLayerInput!) {
            createLayer(input: $input) {
                id
                opacity
                visible
                order
                blending
                renderGraph {
                    root {
                        kind
                        blending
                        children {
                            __typename
                            kind
                            ... on ChannelSourceNode {
                                lens
                                transfer { colormap climMin climMax }
                            }
                        }
                    }
                }
            }
        }
    """
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "opacity": 0.5,
            "order": 2,
            "blending": "NORMAL",
            "renderGraph": {
                "root": {
                    "kind": "blend",
                    "blending": "ADDITIVE",
                    "children": [
                        {"kind": "channel", "lens": str(red.pk), "transfer": {"colormap": "RED", "climMin": 0.0, "climMax": 0.8}},
                        {"kind": "channel", "lens": str(green.pk), "transfer": {"colormap": "GREEN"}},
                    ],
                }
            },
        }
    }

    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors

    data = result.data["createLayer"]
    assert data["opacity"] == 0.5
    assert data["order"] == 2
    assert data["blending"] == "NORMAL"
    children = data["renderGraph"]["root"]["children"]
    assert [c["__typename"] for c in children] == ["ChannelSourceNode", "ChannelSourceNode"]
    assert children[0]["lens"] == str(red.pk), "a node names the single-channel lens it samples"
    assert children[0]["transfer"]["colormap"] == "RED"
    assert children[1]["lens"] == str(green.pk)
    layer_row = await models.Layer.objects.aget(id=data["id"])
    assert {lens.pk async for lens in layer_row.channel_lenses.all()} == {red.pk, green.pk}, "and the layer indexes them, so the delete guard holds them"

    layer = await models.Layer.objects.aget(id=data["id"])
    assert layer.render_graph["root"]["kind"] == "blend"
    assert len(layer.render_graph["root"]["children"]) == 2


_STOPS_CREATE = """
    mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }
"""

_STOPS_QUERY = """
    query Layer($id: ID!) {
        layer(id: $id) {
            ... on ImageLayer {
                renderGraph { root { children { ... on ChannelSourceNode {
                    transfer { gamma climMin climMax stops { position value } }
                } } } }
            }
        }
    }
"""


async def _create_layer_with_transfer(ctx: HttpContext, transfer: dict) -> str:
    """Create a single-channel layer carrying one transfer function, and return its id."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(ctx, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(ctx, lens)
    lens = await seed.channel_lens(ctx, lens)
    result = await schema.execute(
        _STOPS_CREATE,
        context_value=ctx,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel", "transfer": transfer}]}},
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createLayer"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_transfer_stops_round_trip(db, authenticated_context: HttpContext):
    """A hand-authored transfer curve survives the trip through the JSON column intact.

    The mutation response is not enough to prove that: the graph is lowered into the storage
    model, dumped to JSON, stored, and rehydrated on read, and a stop could be dropped at any
    of those four steps while the response still looked right. So the assertions here are on a
    *re-query* and on the stored column, not on what `createLayer` echoed back.

    Positions are raw intensities -- 4000 is an ordinary 12-bit reading -- which is why the
    curve here spans a range no normalized fraction could.
    """
    stops = [{"position": 100.0, "value": 0.0}, {"position": 900.0, "value": 0.8}, {"position": 4000.0, "value": 1.0}]
    layer_id = await _create_layer_with_transfer(authenticated_context, {"colormap": "VIRIDIS", "stops": stops})

    result = await schema.execute(_STOPS_QUERY, context_value=authenticated_context, variable_values={"id": str(layer_id)})
    assert not result.errors, result.errors

    transfer = result.data["layer"]["renderGraph"]["root"]["children"][0]["transfer"]
    assert transfer["stops"] == stops, "the curve comes back in the order it was authored"

    layer = await models.Layer.objects.aget(id=layer_id)
    assert layer.render_graph["root"]["children"][0]["transfer"]["stops"] == stops


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_transfer_without_stops_still_reads(db, authenticated_context: HttpContext):
    """No curve is null, never an empty one -- the shape every render graph written before `stops` existed has.

    `stops` is a new key inside a JSON column that gets no migration and no backfill, so every
    stored graph predates it. A required field, or one defaulting to `[]`, would turn reading
    any of those rows into a validation error at `LayerRenderGraphModel(**self.render_graph)`.
    """
    layer_id = await _create_layer_with_transfer(authenticated_context, {"colormap": "GREY", "gamma": 2.2})

    result = await schema.execute(_STOPS_QUERY, context_value=authenticated_context, variable_values={"id": str(layer_id)})
    assert not result.errors, result.errors

    transfer = result.data["layer"]["renderGraph"]["root"]["children"][0]["transfer"]
    assert transfer["stops"] is None
    assert transfer["gamma"] == 2.2, "with no curve, gamma is still the transfer"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_layer_without_graph_is_rejected(db, authenticated_context: HttpContext):
    """createLayer requires a render graph: the graph is the single source of truth for rendering."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    mutation = "mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }"
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert result.errors, "expected createLayer without a required renderGraph to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_rgb_layer(db, authenticated_context: HttpContext):
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreateRgbLayerInput!) {
            createRgbLayer(input: $input) {
                __typename
                id
                kind
                blending
                redLens { id }
                greenLens { id }
                blueLens { id }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}},
    )
    assert not result.errors, result.errors
    data = result.data["createRgbLayer"]
    # A kind of its own, and no render graph anywhere: as a three-child blend this was
    # indistinguishable from three fluorescence markers somebody tinted red, green and blue.
    assert data["__typename"] == "RgbLayer"
    assert data["kind"] == "RGB"
    assert data["blending"] == "NORMAL"
    assert [data["redLens"]["id"], data["greenLens"]["id"], data["blueLens"]["id"]] == [components["redLens"], components["greenLens"], components["blueLens"]], "three single-channel lenses, each a crop of the layer's lens"

    layer = await models.Layer.objects.aget(id=data["id"])
    assert layer.render_graph is None, "an RGB layer carries its recipe in columns, never a render graph"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_intensity_layer(db, authenticated_context: HttpContext):
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    second = await seed.channel_lens(authenticated_context, lens, 1)

    mutation = """
        mutation Create($input: CreateIntensityLayerInput!) {
            createIntensityLayer(input: $input) {
                __typename
                id
                kind
                lens { id }
                colormap
                gamma
                projectionMode
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(second.id), "colormap": "MAGMA"}},
    )
    assert not result.errors, result.errors
    data = result.data["createIntensityLayer"]
    assert data["__typename"] == "IntensityLayer"
    assert data["kind"] == "INTENSITY"
    assert data["lens"]["id"] == str(second.id), "which channel is drawn is the lens, not a field"
    assert data["colormap"] == "MAGMA"
    # Null, not a mode: this one draws the plane. `createVolumeLayer` is the same kind with
    # this field set, which is the whole difference between them.
    assert data["projectionMode"] is None

    layer = await models.Layer.objects.aget(id=data["id"])
    assert layer.render_graph is None, "the graph form of this was a blend of one child, which said nothing"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_label_layer_without_channel_axis(db, authenticated_context: HttpContext):
    """A label / instance map often has no channel axis: the pixel value itself is the id.

    And the layer that comes back is a LABEL layer, not an image one carrying a flag. It
    holds no render graph at all -- there is no compositing tree over a single field of
    ids, and none of the graph's vocabulary means anything over them.
    """
    axis_names, shape, descriptors = _YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreateLabelLayerInput!) {
            createLabelLayer(input: $input) {
                id
                kind
                blending
                labelRender { background showUnselected selected colorBy { table } }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert not result.errors, result.errors
    data = result.data["createLabelLayer"]
    assert data["kind"] == "LABEL"
    # Adding two objects' colors together would make a third belonging to neither.
    assert data["blending"] == "NORMAL"
    render = data["labelRender"]
    assert render["background"] == 0, "0 is the conventional 'not an object' value, drawn transparent"
    assert render["selected"] == [] and render["showUnselected"] is True
    assert render["colorBy"] is None, "nothing was keyed off this mask, so there is nothing to color by"

    layer = await models.Layer.objects.aget(id=data["id"])
    assert layer.render_graph is None, "a label layer carries its recipe in label_render, never a render graph"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_render_graph_with_projection_node(db, authenticated_context: HttpContext):
    """A render graph may include a projection node (volume rendering mode) over a channel subtree."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreateLayerInput!) {
            createLayer(input: $input) {
                id
                renderGraph { root { children {
                    __typename
                    ... on ProjectionNode { mode children { __typename ... on ChannelSourceNode { lens } } }
                } } }
            }
        }
    """
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "renderGraph": {
                "root": {
                    "kind": "blend",
                    "blending": "ADDITIVE",
                    "children": [
                        {
                            "kind": "projection",
                            "mode": "ATTENUATED_MIP",
                            "children": [{"kind": "channel"}],
                        }
                    ],
                }
            },
        }
    }
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors
    child = result.data["createLayer"]["renderGraph"]["root"]["children"][0]
    assert child["__typename"] == "ProjectionNode"
    assert child["mode"] == "ATTENUATED_MIP"
    assert child["children"][0]["lens"] is None, "no lens named: the node samples the layer's own single-channel lens"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_volume_layer(db, authenticated_context: HttpContext):
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreateVolumeLayerInput!) {
            createVolumeLayer(input: $input) {
                __typename
                id
                kind
                projectionMode
                colormap
                color
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "mode": "VOLUME", "color": [255, 150, 0, 255]}},
    )
    assert not result.errors, result.errors
    data = result.data["createVolumeLayer"]
    # A volume is an intensity layer with a projection, not a kind of its own: a projection
    # collapses z, it does not composite anything, so there is nothing here a graph would say.
    assert data["__typename"] == "IntensityLayer"
    assert data["kind"] == "INTENSITY"
    assert data["projectionMode"] == "VOLUME"
    # It shares the intensity body, so it shares the tint. Asserted rather than assumed:
    # the two mutations take separate input models, and a field added to one and not the
    # other is accepted by the schema and then silently dropped.
    assert data["color"] == [255, 150, 0, 255]
    assert data["colormap"] == "INTENSITY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scene_layers_resolves_imagelayer_polymorphically(db, authenticated_context: HttpContext):
    """Scene.layers returns the polymorphic Layer interface, resolving to the concrete kind.

    This is what the `layer_types` registration buys: a subtype reachable only through the
    interface is dropped from the SDL unless it is listed there, and the failure is a
    resolution error at query time rather than a schema-build one.
    """
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)

    create = "mutation Create($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { id } }"
    result = await schema.execute(
        create,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}},
    )
    assert not result.errors, result.errors
    layer_id = result.data["createRgbLayer"]["id"]

    query = """
        query Scene($id: ID!) {
            scene(id: $id) {
                layers {
                    __typename
                    id
                    opacity
                    ... on RgbLayer {
                        lens { suggestedRenderAxes { x y z intensity } coordinateSystem { axes { name type } } }
                        redLens { id }
                        greenLens { id }
                        blueLens { id }
                    }
                }
            }
        }
    """
    result = await schema.execute(query, context_value=authenticated_context, variable_values={"id": str(scene.id)})
    assert not result.errors, result.errors
    layers = result.data["scene"]["layers"]
    assert len(layers) == 1
    assert layers[0]["__typename"] == "RgbLayer"
    assert layers[0]["id"] == str(layer_id)
    assert [layers[0]["redLens"]["id"], layers[0]["greenLens"]["id"], layers[0]["blueLens"]["id"]] == [components["redLens"], components["greenLens"], components["blueLens"]]

    # Which axis faces the screen is never stored on the layer: a viewer reads the roles off
    # the lens' axis TYPES and the spatial slots off the placement. The convention the server
    # suggests takes x as the LAST spatial axis: the old stored rule took the first, and for
    # these (c, y, x) axes it wrote xDim="y".
    assert layers[0]["lens"]["suggestedRenderAxes"] == {"x": "x", "y": "y", "z": None, "intensity": "c"}
    assert [axis["type"] for axis in layers[0]["lens"]["coordinateSystem"]["axes"]] == ["CHANNEL", "SPACE", "SPACE"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_layer_scoping_seam_is_the_scene(db, authenticated_context: HttpContext, other_org_context: HttpContext):
    """The org-scoping seam (get_for_org) resolves through the scene, not the lens.

    Layer has only nullable source FKs (lens/annotation collection/table/mesh); org isolation for
    single-object access must go through the (required) scene FK. Assert both the
    resolved path and that another org cannot fetch the layer via get_for_org.
    """
    from asgiref.sync import sync_to_async
    from core.scoping import organization_path, get_for_org

    # The required scene FK is authoritative (the source FKs are nullable and skipped).
    assert organization_path(models.Layer) == "scene__organization"

    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)
    create = "mutation Create($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { id } }"
    result = await schema.execute(
        create,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}},
    )
    assert not result.errors, result.errors
    layer_id = result.data["createRgbLayer"]["id"]

    class _Info:
        def __init__(self, ctx):
            self.context = ctx

    mine = await sync_to_async(get_for_org)(models.Layer, _Info(authenticated_context), id=layer_id)
    assert str(mine.id) == str(layer_id)

    with pytest.raises(models.Layer.DoesNotExist):
        await sync_to_async(get_for_org)(models.Layer, _Info(other_org_context), id=layer_id)


# ---------------------------------------------------------------------------
# Phasor nodes
#
# A phasor node *consumes* an axis rather than indexing into it: it reduces the
# whole profile along a MICROTIME (FLIM arrival time) or SPECTRUM (wavelength)
# axis to a single (g, s) and colors the pixel by it. Its output is a raster that
# composites into the scene like any other leaf -- there is no plot.
# ---------------------------------------------------------------------------

# A FLIM cube: an arrival-time axis, and one detection channel. Ordered by type
# (channel and microtime rank together, before space).
_C_TAU_YX = (
    ["c", "tau", "y", "x"],
    [2, 16, 32, 32],
    [
        seed.axis("c", enums.AxisType.CHANNEL),
        seed.axis("tau", enums.AxisType.MICROTIME),
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ],
)

# A hyperspectral cube: a wavelength axis, no detection channel.
_LAMBDA_YX = (
    ["lambda", "y", "x"],
    [24, 32, 32],
    [
        seed.axis("lambda", enums.AxisType.SPECTRUM),
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ],
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_render_graph_with_phasor_node(db, authenticated_context: HttpContext):
    """A render graph may include a phasor node reducing the microtime axis."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens, 1)
    detector = await seed.channel_lens(authenticated_context, lens, 0)

    mutation = """
        mutation Create($input: CreateLayerInput!) {
            createLayer(input: $input) {
                id
                renderGraph { root { children {
                    __typename
                    ... on PhasorNode {
                        phasorAxis
                        harmonic
                        lens
                        transfer { mode colormap min max weightByIntensity cursors { kind g s radius color label } }
                    }
                } } }
            }
        }
    """
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "renderGraph": {
                "root": {
                    "kind": "blend",
                    "blending": "NORMAL",
                    "children": [
                        {
                            "kind": "phasor",
                            "phasorAxis": "tau",
                            "harmonic": 2,
                            "lens": str(detector.pk),
                            "phasorTransfer": {
                                "mode": "MODULATION",
                                "colormap": "VIRIDIS",
                                "min": "0.5 ns",
                                "max": "4 ns",
                                "cursors": [{"kind": "CIRCLE", "g": 0.4, "s": 0.3, "radius": 0.05, "color": [255, 0, 0, 255], "label": "bound"}],
                            },
                        }
                    ],
                }
            },
        }
    }
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors

    node = result.data["createLayer"]["renderGraph"]["root"]["children"][0]
    assert node["__typename"] == "PhasorNode"
    assert node["phasorAxis"] == "tau"
    assert node["harmonic"] == 2
    assert node["lens"] == str(detector.pk), "the detection channel is a lens, named by the node"
    assert node["transfer"]["mode"] == "MODULATION"
    assert node["transfer"]["colormap"] == "VIRIDIS"
    assert node["transfer"]["weightByIntensity"] is True

    # The bounds keep their unit: over a microtime axis they are durations, and it is the
    # unit -- not the field -- that says so.
    assert "nanosecond" in node["transfer"]["min"]
    assert node["transfer"]["cursors"][0]["label"] == "bound"
    assert node["transfer"]["cursors"][0]["radius"] == 0.05

    layer = await models.Layer.objects.aget(id=result.data["createLayer"]["id"])
    stored = await sync_to_async(lambda: layer.render_graph["root"]["children"][0])()
    assert stored["kind"] == "phasor"
    assert stored["phasor_axis"] == "tau"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_node_over_a_spectrum_axis(db, authenticated_context: HttpContext):
    """The generalization is real, not nominal: the same node reduces a wavelength axis."""
    axis_names, shape, descriptors = _LAMBDA_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreatePhasorLayerInput!) {
            createPhasorLayer(input: $input) {
                __typename
                id
                kind
                blending
                phasorRender { phasorAxis harmonic transfer { min } }
            }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        # No phasorAxis: it defaults to the lens' only phasor-capable axis.
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "transfer": {"min": "480 nm", "max": "620 nm"}}},
    )
    assert not result.errors, result.errors

    data = result.data["createPhasorLayer"]
    assert data["__typename"] == "PhasorLayer"
    assert data["kind"] == "PHASOR"
    render = data["phasorRender"]
    assert render["phasorAxis"] == "lambda"
    assert "nanometer" in render["transfer"]["min"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_phasor_layer_is_an_overlay(db, authenticated_context: HttpContext):
    """The layer alpha-composites over what is beneath it. A hue carrying a lifetime is not
    something you *add* to the layers underneath -- that is what an intensity is."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreatePhasorLayerInput!) {
            createPhasorLayer(input: $input) { id blending opacity }
        }
    """
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert not result.errors, result.errors
    assert result.data["createPhasorLayer"]["blending"] == "NORMAL"

    layer = await models.Layer.objects.aget(id=result.data["createPhasorLayer"]["id"])
    render = await sync_to_async(lambda: layer.phasor_render)()
    assert layer.render_graph is None, "a phasor layer carries its recipe in phasor_render, never a render graph"
    assert render["harmonic"] == 1
    # The detection channel is the lens' selection, so the recipe carries no channel field at all.
    assert set(render) == {"phasor_axis", "harmonic", "transfer"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_node_inside_a_projection_node(db, authenticated_context: HttpContext):
    """A phasor-colored volume, MIP'd into the scene's z. The phasor consumes tau; z stays free."""
    axis_names = ["tau", "z", "y", "x"]
    shape = [16, 8, 32, 32]
    descriptors = [
        seed.axis("tau", enums.AxisType.MICROTIME),
        seed.axis("z", enums.AxisType.SPACE),
        seed.axis("y", enums.AxisType.SPACE),
        seed.axis("x", enums.AxisType.SPACE),
    ]
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    mutation = """
        mutation Create($input: CreateLayerInput!) {
            createLayer(input: $input) {
                renderGraph { root { children {
                    __typename
                    ... on ProjectionNode { mode children { __typename ... on PhasorNode { phasorAxis } } }
                } } }
            }
        }
    """
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "renderGraph": {
                "root": {
                    "kind": "blend",
                    "blending": "NORMAL",
                    "children": [{"kind": "projection", "mode": "MIP", "children": [{"kind": "phasor", "phasorAxis": "tau"}]}],
                }
            },
        }
    }
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors

    projection = result.data["createLayer"]["renderGraph"]["root"]["children"][0]
    assert projection["__typename"] == "ProjectionNode"
    assert projection["children"][0]["__typename"] == "PhasorNode"
    assert projection["children"][0]["phasorAxis"] == "tau"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_axis", ["c", "x"])
async def test_phasor_axis_must_be_a_phasor_axis(db, authenticated_context: HttpContext, bad_axis: str):
    """A DFT over a channel or a spatial axis produces a (g, s) that means nothing, and nothing
    downstream could tell it from a real phasor. So it is rejected here."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = "mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }"
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "phasor", "phasorAxis": bad_axis}]}},
        }
    }
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert result.errors, f"expected a phasor over the {bad_axis!r} axis to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    [
        {"kind": "CIRCLE", "g": 0.4, "s": 0.3},  # no radius: selects nothing
        {"kind": "CIRCLE", "g": 0.4, "s": 0.3, "radius": 0},  # a point: selects nothing
        {"kind": "POLYGON", "points": [[0.1, 0.1], [0.2, 0.2]]},  # a line: selects nothing
    ],
)
async def test_degenerate_phasor_cursor_is_rejected(db, authenticated_context: HttpContext, cursor: dict):
    """A cursor that selects no region is not a degenerate color rule, it is one that silently
    never fires -- while still appearing in the response the client reads back."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    mutation = "mutation Create($input: CreatePhasorLayerInput!) { createPhasorLayer(input: $input) { id } }"
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "transfer": {"cursors": [cursor]}}},
    )
    assert result.errors, f"expected the degenerate cursor {cursor} to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_harmonic_must_be_positive(db, authenticated_context: HttpContext):
    """There is no zeroth harmonic: it is the DC term, which is the total photon count."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = "mutation Create($input: CreatePhasorLayerInput!) { createPhasorLayer(input: $input) { id } }"
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "harmonic": 0}},
    )
    assert result.errors, "expected harmonic 0 to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_phasor_layer_needs_a_phasor_axis(db, authenticated_context: HttpContext):
    """A plain c/y/x stack has nothing to take a phasor over, and says so."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    mutation = "mutation Create($input: CreatePhasorLayerInput!) { createPhasorLayer(input: $input) { id } }"
    result = await schema.execute(
        mutation,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id)}},
    )
    assert result.errors, "expected a phasor layer over a lens with no MICROTIME/SPECTRUM axis to be rejected"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_moving_a_layer_to_another_scene_meets_the_same_placement_gate(db, authenticated_context: HttpContext):
    """Rebinding a layer into a scene is the same claim creating it there makes.

    An update that only touches styling never re-litigates the placement; an update that
    changes the scene is refused until the source is registered into the new world.
    """
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    create = "mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }"
    created = await schema.execute(
        create,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel"}]}},
            }
        },
    )
    assert not created.errors, created.errors
    layer_id = created.data["createLayer"]["id"]

    update = "mutation Update($input: UpdateLayerInput!) { updateLayer(input: $input) { id opacity } }"

    # A styling-only update never re-checks the placement.
    styled = await schema.execute(update, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "opacity": 0.5}})
    assert not styled.errors, styled.errors
    assert styled.data["updateLayer"]["opacity"] == 0.5

    # Moving into a scene whose world the source does not reach is refused...
    other = await seed.create_scene(authenticated_context, "Elsewhere")
    moved = await schema.execute(update, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "scene": str(other.pk)}})
    assert moved.errors, "rebinding into a scene with no path is the unplaced-layer case again"
    assert "createTransformation" in str(moved.errors[0])

    # ...and allowed once someone registers it there.
    await seed.register_into_scene(authenticated_context, other, lens.dataset)
    moved = await schema.execute(update, context_value=authenticated_context, variable_values={"input": {"id": layer_id, "scene": str(other.pk)}})
    assert not moved.errors, moved.errors


# ---------------------------------------------------------------------------
# The fixed-shape kinds: their patches, and the refusals that keep a row from
# carrying two recipes at once.
# ---------------------------------------------------------------------------


async def _seed_intensity_layer(ctx: HttpContext, **overrides) -> dict:
    """An intensity layer over one channel of a 3-channel dataset, and the lens and scene behind it."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(ctx, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(ctx, lens)
    lens = await seed.channel_lens(ctx, lens)
    result = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **overrides}},
    )
    assert not result.errors, result.errors
    return {"id": result.data["createIntensityLayer"]["id"], "lens": lens, "scene": scene}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_intensity_patch_leaves_what_it_does_not_name(db, authenticated_context: HttpContext):
    """The patch discipline `updateLabelLayer` set: changing a colormap is not a reason to
    lose the contrast limits somebody spent a minute setting."""
    seeded = await _seed_intensity_layer(authenticated_context, climMin=100.0, climMax=900.0, gamma=2.0)

    result = await schema.execute(
        """
        mutation M($input: UpdateIntensityLayerInput!) {
            updateIntensityLayer(input: $input) { id colormap climMin climMax gamma name }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"id": seeded["id"], "colormap": "PLASMA", "name": "DAPI"}},
    )
    assert not result.errors, result.errors
    data = result.data["updateIntensityLayer"]
    assert data["colormap"] == "PLASMA"
    assert data["name"] == "DAPI"
    assert (data["climMin"], data["climMax"], data["gamma"]) == (100.0, 900.0, 2.0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_intensity_layer_carries_a_solid_tint(db, authenticated_context: HttpContext):
    """A colour that is a measured fact rather than a choice off a list.

    A converter reading a channel's emission wavelength, or the colour the acquisition
    software saved, holds an RGBA quad and no named map that means it. Before this field
    that one fact forced the whole layer onto a render graph -- a blend node with a single
    child, which is the shape `IntensityLayer` exists to replace.
    """
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    lens = await seed.channel_lens(authenticated_context, lens)

    result = await schema.execute(
        """
        mutation Create($input: CreateIntensityLayerInput!) {
            createIntensityLayer(input: $input) { id kind colormap color }
        }
        """,
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "color": [0, 255, 200, 255]}},
    )
    assert not result.errors, result.errors
    data = result.data["createIntensityLayer"]
    assert data["kind"] == "INTENSITY"
    assert data["color"] == [0, 255, 200, 255]
    # Either/or: the tint stands in for the colormap, so the map stored beside it is the one
    # that adds no hue of its own -- not the grey default, which the layer would never draw.
    assert data["colormap"] == "INTENSITY"

    layer = await models.Layer.objects.aget(id=data["id"])
    assert layer.render_graph is None, "a tint is a field now, not a reason to author a graph"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tint_patch_takes_the_colormap_with_it(db, authenticated_context: HttpContext):
    """Either/or holds on the row, not only within one input: tinting a layer that was drawn
    through MAGMA leaves no MAGMA behind for a client to read back and believe."""
    seeded = await _seed_intensity_layer(authenticated_context, colormap="MAGMA")

    result = await schema.execute(_UPDATE_TINT, context_value=authenticated_context, variable_values={"input": {"id": seeded["id"], "color": [255, 0, 255, 255]}})
    assert not result.errors, result.errors
    data = result.data["updateIntensityLayer"]
    assert data["color"] == [255, 0, 255, 255]
    assert data["colormap"] == "INTENSITY"


_UPDATE_TINT = """
    mutation M($input: UpdateIntensityLayerInput!) {
        updateIntensityLayer(input: $input) { id colormap color }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["createIntensityLayer", "createVolumeLayer"])
async def test_a_tint_beside_another_colormap_is_refused(db, authenticated_context: HttpContext, mutation: str):
    """Two answers to one question, refused rather than resolved -- on both mutations that
    share the intensity body, since they take separate input models."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    result = await schema.execute(
        f"mutation M($input: {mutation[0].upper()}{mutation[1:]}Input!) {{ {mutation}(input: $input) {{ id }} }}",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "color": [0, 255, 200, 255], "colormap": "MAGMA"}},
    )
    assert result.errors, "a tint and a colormap were both accepted"
    assert "Set either `color` or `colormap`" in result.errors[0].message
    assert not await models.Layer.objects.filter(scene=scene).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tint_beside_the_intensity_colormap_is_the_same_statement(db, authenticated_context: HttpContext):
    """INTENSITY is what a tint's colormap is, so saying both is saying it twice. A client
    echoing a tinted layer back -- `colormap` and `color` as it read them -- must not be refused."""
    seeded = await _seed_intensity_layer(authenticated_context, color=[0, 255, 200, 255], colormap="INTENSITY")

    result = await schema.execute(_UPDATE_TINT, context_value=authenticated_context, variable_values={"input": {"id": seeded["id"], "color": [255, 0, 0, 255], "colormap": "INTENSITY"}})
    assert not result.errors, result.errors
    assert result.data["updateIntensityLayer"] == {"id": seeded["id"], "colormap": "INTENSITY", "color": [255, 0, 0, 255]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_tint_patch_refuses_another_colormap_and_changes_nothing(db, authenticated_context: HttpContext):
    seeded = await _seed_intensity_layer(authenticated_context, colormap="MAGMA")

    result = await schema.execute(_UPDATE_TINT, context_value=authenticated_context, variable_values={"input": {"id": seeded["id"], "color": [255, 0, 255, 255], "colormap": "PLASMA"}})
    assert result.errors, "a tint and a colormap were both accepted"
    assert "Set either `color` or `colormap`" in result.errors[0].message

    layer = await models.Layer.objects.aget(id=seeded["id"])
    assert (layer.colormap, layer.color) == ("magma", None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colormap_patch_replaces_the_tint(db, authenticated_context: HttpContext):
    """The way back from a tint. A null `color` means "unchanged", so it cannot be the
    spelling for "no tint" -- naming a colormap is, because the two are either/or."""
    seeded = await _seed_intensity_layer(authenticated_context, color=[0, 255, 200, 255])

    result = await schema.execute(_UPDATE_TINT, context_value=authenticated_context, variable_values={"input": {"id": seeded["id"], "colormap": "MAGMA"}})
    assert not result.errors, result.errors
    assert result.data["updateIntensityLayer"] == {"id": seeded["id"], "colormap": "MAGMA", "color": None}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_patch_that_names_neither_keeps_the_tint(db, authenticated_context: HttpContext):
    """The patch discipline: a gamma change is not a reason to lose the tint, and neither is
    restating the INTENSITY colormap the tint already carries."""
    seeded = await _seed_intensity_layer(authenticated_context, color=[0, 255, 200, 255])

    for patch in ({"gamma": 2.0}, {"colormap": "INTENSITY"}):
        result = await schema.execute(_UPDATE_TINT, context_value=authenticated_context, variable_values={"input": {"id": seeded["id"], **patch}})
        assert not result.errors, result.errors
        assert result.data["updateIntensityLayer"] == {"id": seeded["id"], "colormap": "INTENSITY", "color": [0, 255, 200, 255]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("color", [[255], [255, 0, 0], [255, 0, 0, 255, 255], [255, 0, 0, 300]])
async def test_an_intensity_tint_is_rgba_or_nothing(db, authenticated_context: HttpContext, color: list):
    """Four components, each 0..255 -- the same refusal `TransferFunctionInput.color` makes.

    Worth checking here rather than trusting the shared validator: the tint reaches the row
    through a different input model, and a field wired up without its validator would accept
    a three-component colour that no renderer can read.
    """
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)

    result = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), "color": color}},
    )
    assert result.errors, f"{color} is not an RGBA colour"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_layer_refuses_a_flat_kind_and_names_the_one_that_wants_it(db, authenticated_context: HttpContext):
    """A render graph is not an intensity layer's vocabulary, and the refusal says where to go.

    The mirror of `test_update_label_layer_refuses_an_image_layer`, and load-bearing for the
    same reason: without it one call writes a graph onto a flat row and leaves it carrying
    two recipes at once.
    """
    seeded = await _seed_intensity_layer(authenticated_context)

    result = await schema.execute(
        """
        mutation M($input: UpdateLayerInput!) { updateLayer(input: $input) { id } }
        """,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "id": seeded["id"],
                "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel"}]}},
            }
        },
    )
    assert result.errors
    message = str(result.errors[0])
    assert "not its render vocabulary" in message
    assert "updateIntensityLayer" in message

    # The refusal left one recipe, not two.
    layer = await models.Layer.objects.aget(id=seeded["id"])
    assert layer.render_graph is None
    assert layer.kind == enums.LayerKind.INTENSITY.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_intensity_layer_refuses_an_image_layer(db, authenticated_context: HttpContext):
    """And back the other way: a general image layer's graph is not patched field by field."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    created = await schema.execute(
        "mutation M($input: CreateLayerInput!) { createLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={
            "input": {
                "scene": str(scene.id),
                "lens": str(lens.id),
                "renderGraph": {
                    "root": {
                        "kind": "blend",
                        "children": [
                            {"kind": "channel", "lens": str((await seed.channel_lens(authenticated_context, lens, 0)).pk)},
                            {"kind": "channel", "lens": str((await seed.channel_lens(authenticated_context, lens, 1)).pk)},
                        ],
                    }
                },
            }
        },
    )
    assert not created.errors, created.errors

    result = await schema.execute(
        "mutation M($input: UpdateIntensityLayerInput!) { updateIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createLayer"]["id"], "colormap": "PLASMA"}},
    )
    assert result.errors
    message = str(result.errors[0])
    assert "not an intensity layer" in message
    assert "updateLayer" in message


_RGB_WB = "fragment WB on RgbLayer { id whiteBalance }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_rgb_white_balance_is_optional_set_and_patched(db, authenticated_context: HttpContext):
    """Null by default; set on create; a patch that omits it keeps it; [1, 1, 1] resets it."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)
    create = _RGB_WB + " mutation M($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { ...WB } }"
    update = _RGB_WB + " mutation M($input: UpdateRgbLayerInput!) { updateRgbLayer(input: $input) { ...WB } }"

    result = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}})
    assert not result.errors, result.errors
    assert result.data["createRgbLayer"]["whiteBalance"] is None

    result = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components, "whiteBalance": [1.8, 1.0, 1.4]}})
    assert not result.errors, result.errors
    layer = result.data["createRgbLayer"]
    assert layer["whiteBalance"] == [1.8, 1.0, 1.4]

    result = await schema.execute(update, context_value=authenticated_context, variable_values={"input": {"id": layer["id"], "climMax": 200.0}})
    assert not result.errors, result.errors
    assert result.data["updateRgbLayer"]["whiteBalance"] == [1.8, 1.0, 1.4], "a patch that omits it keeps it"

    result = await schema.execute(update, context_value=authenticated_context, variable_values={"input": {"id": layer["id"], "whiteBalance": [1, 1, 1]}})
    assert not result.errors, result.errors
    assert result.data["updateRgbLayer"]["whiteBalance"] == [1.0, 1.0, 1.0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gains", "expected"),
    [([1.0, 1.0], "exactly 3"), ([1.0, 1.0, 1.0, 1.0], "exactly 3"), ([1.0, 0.0, 1.0], "green gain is 0"), ([-1.0, 1.0, 1.0], "red gain is -1")],
)
async def test_rgb_refuses_a_malformed_white_balance(db, authenticated_context: HttpContext, gains: list, expected: str):
    """Three positive gains or nothing -- on create and on update alike."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)

    result = await schema.execute(
        "mutation M($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components, "whiteBalance": gains}},
    )
    assert result.errors and expected in str(result.errors[0])

    result = await schema.execute(
        "mutation M($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}},
    )
    result = await schema.execute(
        "mutation M($input: UpdateRgbLayerInput!) { updateRgbLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": result.data["createRgbLayer"]["id"], "whiteBalance": gains}},
    )
    assert result.errors and expected in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_flat_layer_still_walks_its_pyramid(db, authenticated_context: HttpContext):
    """`levelPaths` is a fact about the array, so every lens-backed kind answers it.

    ABLATION: leave `level_placements` keyed on the two kinds it knew before this split and
    an intensity layer returns an empty list rather than its levels -- silently, because an
    unregistered layer legitimately returns nothing.
    """
    seeded = await _seed_intensity_layer(authenticated_context)

    result = await schema.execute(
        """
        query Scene($id: ID!) {
            scene(id: $id) {
                layers {
                    __typename
                    placement
                    ... on IntensityLayer { levelPaths { dataArray { id } } }
                }
            }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(seeded["scene"].id)},
    )
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    assert layer["__typename"] == "IntensityLayer"
    # The placement is the other half of the same fact: a kind the source-system walk does
    # not know reads UNREGISTERED, which is indistinguishable from nobody having registered it.
    assert layer["placement"] == "PLACED"
    assert len(layer["levelPaths"]) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_channel_node_samples_exactly_one_channel(db, authenticated_context: HttpContext):
    """A node draws one channel, and which one is a lens: a lens keeping three channels is refused, naming the cut that pins one."""
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    mutation = "mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }"
    for node in ({"kind": "channel"}, {"kind": "channel", "lens": str(lens.id)}):
        variables = {"input": {"scene": str(scene.id), "lens": str(lens.id), "renderGraph": {"root": {"kind": "blend", "children": [node]}}}}
        result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
        assert result.errors, node
        message = str(result.errors[0])
        assert "pin one channel" in message and "createLens" in message, message
    # A crop of another dataset is not a channel of this one, however many channels it keeps.
    other = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    stranger = await seed.channel_lens(authenticated_context, other)
    variables = {"input": {"scene": str(scene.id), "lens": str(lens.id), "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel", "lens": str(stranger.pk)}]}}}}
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert result.errors and "crop of the layer's own lens" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pinning_tau_is_not_pinning_a_channel(db, authenticated_context: HttpContext):
    """The pre-existing guarantee must not regress: a channel is a CHANNEL-axis position. A lens that
    pins one arrival-time bin and keeps both detectors has not picked a channel, and is refused as such."""
    axis_names, shape, descriptors = _C_TAU_YX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    one_bin = await seed.channel_lens(authenticated_context, lens, 0, axis="tau")
    mutation = "mutation Create($input: CreateLayerInput!) { createLayer(input: $input) { id } }"
    variables = {
        "input": {
            "scene": str(scene.id),
            "lens": str(lens.id),
            "renderGraph": {"root": {"kind": "blend", "children": [{"kind": "channel", "lens": str(one_bin.pk)}]}},
        }
    }
    result = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert result.errors, "pinning tau is not pinning a channel"
    assert "CHANNEL axis 'c'" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["redLens", "greenLens", "blueLens"])
async def test_an_rgb_component_is_a_single_channel_crop_of_the_layers_lens(db, authenticated_context: HttpContext, field: str):
    """Each of the three is checked, on create and on update, and the refusal names the one that was wrong.

    A component that keeps three channels has not picked one; a crop of another dataset is not
    a crop of this lens, whatever it keeps.
    """
    axis_names, shape, descriptors = _CYX
    lens = await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors)
    scene = await _seed_scene(authenticated_context, lens)
    components = await _rgb_lenses(authenticated_context, lens)
    stranger = await seed.channel_lens(authenticated_context, await _seed_lens(authenticated_context, axis_names=axis_names, shape=shape, descriptors=descriptors))
    create = "mutation M($input: CreateRgbLayerInput!) { createRgbLayer(input: $input) { id } }"
    for wrong, expected in ((str(lens.id), "pin one channel"), (str(stranger.pk), "crop of the layer's own lens")):
        result = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components, field: wrong}})
        assert result.errors, (field, wrong)
        message = str(result.errors[0])
        assert expected in message
        # snake_case: the field name as the input model spells it, not the GraphQL camelCase.
        assert field.replace("Lens", "_lens") in message
    created = await schema.execute(create, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.id), "lens": str(lens.id), **components}})
    assert not created.errors, created.errors
    patched = await schema.execute(
        "mutation M($input: UpdateRgbLayerInput!) { updateRgbLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"id": created.data["createRgbLayer"]["id"], field: str(stranger.pk)}},
    )
    assert patched.errors and field.replace("Lens", "_lens") in str(patched.errors[0])
