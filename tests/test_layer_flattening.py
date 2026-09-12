"""What the flattening migration will and will not rewrite.

The migration (`core/migrations/0004_flatten_single_source_layers`) turns every image layer
whose render graph is a blend of one source into the flat kind that source is. Its refusals
carry more weight than its rewrites -- a wrong rewrite silently changes what somebody's data
looks like, and there is no error to notice -- so this file is mostly refusals.

The decision function is pure and takes a graph dict, which is why these run without a
database: the risky part is the classification, not the loop that saves.
"""

import importlib

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from tests import seed

migration = importlib.import_module("core.migrations.0004_flatten_single_source_layers")
_flatten = migration._flatten


def _blend(*children, blending: str = "additive", label: str | None = None) -> dict:
    return {"root": {"kind": "blend", "blending": blending, "label": label, "children": list(children)}}


def _channel(index: int = 0, axis: str | None = "c", label: str | None = None, **transfer) -> dict:
    return {"kind": "channel", "intensity_axis": axis, "intensity_index": index, "label": label, "transfer": transfer}


# --- what it rewrites ------------------------------------------------------------


def test_a_blend_of_one_channel_is_an_intensity_layer():
    """The shape `createIntensityLayer` used to build: a blend node with a single child.

    Additively blending one thing is that thing, so the root carried no information -- which
    is the whole reason this migration exists.
    """
    flat = _flatten(_blend(_channel(1, colormap="magma", clim_min=10.0, clim_max=900.0, gamma=2.0)))

    assert flat["kind"] == "intensity"
    assert flat["intensity_axis"] == "c"
    assert flat["intensity_index"] == 1
    assert (flat["colormap"], flat["clim_min"], flat["clim_max"], flat["gamma"]) == ("magma", 10.0, 900.0, 2.0)
    assert flat["projection_mode"] is None, "no projection node, so it draws the plane"
    assert flat["render_graph"] is None, "the recipe moved; leaving the graph would be two copies of it"


def test_a_projection_over_one_channel_keeps_its_mode():
    """A volume is an intensity layer with a mode, not a kind of its own."""
    flat = _flatten(_blend({"kind": "projection", "mode": "mip", "children": [_channel(0, colormap="grey")]}))

    assert flat["kind"] == "intensity"
    assert flat["projection_mode"] == "mip"
    assert flat["intensity_index"] == 0


def test_a_blend_of_one_phasor_is_a_phasor_layer():
    node = {"kind": "phasor", "phasor_axis": "tau", "intensity_axis": "c", "intensity_index": 1, "harmonic": 2, "transfer": {"mode": "PHASE"}}
    flat = _flatten(_blend(node))

    assert flat["kind"] == "phasor"
    assert flat["phasor_render"]["phasor_axis"] == "tau"
    assert flat["phasor_render"]["harmonic"] == 2
    assert flat["render_graph"] is None


def test_the_channels_label_becomes_the_layers_name():
    """The handle a viewer had on a bootstrapped layer survives the move.

    The channel's own label first -- that is where the bootstrap wrote "DAPI" -- and the
    root's as the fallback, which is where the single-channel builders put theirs.
    """
    assert _flatten(_blend(_channel(label="DAPI"), label="ignored"))["name"] == "DAPI"
    assert _flatten(_blend(_channel(label=None), label="intensity"))["name"] == "intensity"


# --- what it refuses -------------------------------------------------------------


def test_a_three_channel_red_green_blue_blend_is_left_alone():
    """The refusal this migration is really about.

    This is the shape `createRgbLayer` used to write -- and it is *also* the shape three
    fluorescence markers tinted red, green and blue have, which is the commoner reading and
    the reason RGB is never inferred. Flattening it would fuse three signals into one picture
    where none of them could be hidden or reordered again, silently and irreversibly.
    """
    graph = _blend(
        _channel(0, label="red", colormap="red"),
        _channel(1, label="green", colormap="green"),
        _channel(2, label="blue", colormap="blue"),
        label="rgb",
    )
    assert _flatten(graph) is None


def test_a_multi_channel_composite_is_left_alone():
    """Two channels in one layer is what a render graph is *for*."""
    assert _flatten(_blend(_channel(0, colormap="green"), _channel(1, colormap="magenta"))) is None


def test_an_authored_transfer_curve_is_left_alone():
    """`stops` is the general case of the contrast window, and a flat layer has no column for it.

    ABLATION: drop this check and the curve is discarded on migrate -- the layer keeps
    rendering, at the wrong contrast, with nothing to say what changed.
    """
    assert _flatten(_blend(_channel(colormap="grey", stops=[{"position": 0.0, "value": 0.0}, {"position": 4000.0, "value": 1.0}]))) is None


def test_an_inverted_or_tinted_channel_is_left_alone():
    """`invert` has no column on an intensity layer, and at 0004 neither did `color`.

    A tint has one now, so the second assertion is a statement about *this migration*
    rather than about the schema: 0004 is applied history and classifies what was true
    when it ran. A tinted single-channel graph written before it stays an IMAGE layer,
    and nothing re-runs to reclassify it -- which is the whole reason this migration is
    left frozen rather than taught the new column.
    """
    assert _flatten(_blend(_channel(colormap="grey", invert=True))) is None
    assert _flatten(_blend(_channel(color=[255, 0, 0, 255]))) is None


def test_a_per_channel_opacity_that_says_something_is_left_alone():
    """A second alpha the flat kind cannot hold.

    1.0 says nothing and is safe to drop; anything else is a value the author wrote, and
    folding it into the layer's own alpha would be the migration inventing a number.
    """
    assert _flatten(_blend(_channel(colormap="grey", opacity=1.0))) is not None
    assert _flatten(_blend(_channel(colormap="grey", opacity=0.4))) is None


def test_a_non_additive_root_is_left_alone():
    """Every builder wrote ADDITIVE, and blending one child means it anyway.

    A root saying something else was authored by hand, about a composite that is not there.
    Refusing beats silently dropping the mode.
    """
    assert _flatten(_blend(_channel(colormap="grey"), blending="multiplicative")) is None


def test_a_projected_phasor_is_left_alone():
    """A PHASOR layer has no projection column. Rare to the point of hypothetical, and still not a reason to drop it."""
    node = {"kind": "phasor", "phasor_axis": "tau", "harmonic": 1, "transfer": {}}
    assert _flatten(_blend({"kind": "projection", "mode": "mip", "children": [node]})) is None


def test_a_graph_that_is_not_rooted_in_a_blend_is_left_alone():
    """Defensive: the column's invariant is a blend root, and a migration is the wrong place to discover it is not."""
    assert _flatten({"root": _channel()}) is None
    assert _flatten({}) is None
    assert _flatten({"root": {"kind": "blend", "children": []}}) is None


# --- the loop, against a real table ----------------------------------------------
#
# The classification above is the risky half, and it is pure. The loop is the half that can
# only fail at `manage.py migrate` time: a wrong name in `update_fields` raises there and
# nowhere else, which is the worst possible place to find out. One round trip covers it.



class _RealApps:
    """`apps.get_model` as the migration sees it, backed by the live models.

    The historical model at 0004 has exactly the fields the live one does -- 0003 added them
    and nothing has changed them since -- so substituting it exercises the same attribute
    names `update_fields` will be given at migrate time, which is the point of this test.
    """

    @staticmethod
    def get_model(app_label: str, model_name: str):
        assert (app_label, model_name) == ("core", "Layer")
        return models.Layer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_migration_round_trips_one_layer(db, authenticated_context: HttpContext):
    """Forward and back, against the real table: every name in `update_fields` is a real column."""
    scene = await seed.create_scene(authenticated_context, "Flattening")

    def create() -> models.Layer:
        return models.Layer.objects.create(
            scene=scene,
            kind=enums.LayerKind.IMAGE.value,
            render_graph=_blend(_channel(1, label="DAPI", colormap="magma", clim_min=5.0, clim_max=600.0, gamma=1.5)),
        )

    layer = await sync_to_async(create)()

    await sync_to_async(migration.flatten_layers)(_RealApps, None)
    layer = await models.Layer.objects.aget(pk=layer.pk)
    assert layer.kind == enums.LayerKind.INTENSITY.value
    assert layer.render_graph is None
    assert layer.name == "DAPI"
    assert (layer.colormap, layer.clim_min, layer.clim_max, layer.gamma) == ("magma", 5.0, 600.0, 1.5)
    assert layer.intensity_index == 1

    await sync_to_async(migration.unflatten_layers)(_RealApps, None)
    layer = await models.Layer.objects.aget(pk=layer.pk)
    assert layer.kind == enums.LayerKind.IMAGE.value
    channel = layer.render_graph["root"]["children"][0]
    assert channel["kind"] == "channel"
    assert channel["intensity_index"] == 1
    assert channel["transfer"]["colormap"] == "magma"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_migration_leaves_a_composite_untouched(db, authenticated_context: HttpContext):
    """The refusals hold against the table too, not just against the classifier."""
    scene = await seed.create_scene(authenticated_context, "Composite")

    def create() -> models.Layer:
        return models.Layer.objects.create(
            scene=scene,
            kind=enums.LayerKind.IMAGE.value,
            render_graph=_blend(_channel(0, colormap="green"), _channel(1, colormap="magenta")),
        )

    layer = await sync_to_async(create)()
    before = layer.render_graph

    await sync_to_async(migration.flatten_layers)(_RealApps, None)
    layer = await models.Layer.objects.aget(pk=layer.pk)
    assert layer.kind == enums.LayerKind.IMAGE.value
    assert layer.render_graph == before, "a layer that genuinely composites keeps its graph, byte for byte"
