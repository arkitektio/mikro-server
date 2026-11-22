"""View settings a client set and lost on reload: a track's tail, a chart layer's value range, a colour gradient.

Each is written by the layer's own update (or inside its render graph) and read back from a
fresh query, so what a person tuned is what the next session opens.
"""

import pytest
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_chart_values import POINTS, _nuclei, _ok, _refused, _values_chart
from tests.test_layer_kinds import _seed_scene, _seed_table_dataset
from tests.test_layer_mutations import _STOPS_QUERY, _create_layer_with_transfer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_track_layer_keeps_its_tail(authenticated_context: HttpContext) -> None:
    ctx = authenticated_context
    scene = await _seed_scene(ctx)
    dataset, system = await _seed_table_dataset(ctx, "tails", with_track=True)
    await seed.register_into_scene(ctx, scene, system=system)
    created = await _ok(
        ctx,
        "mutation C($input: CreateTrackLayerInput!) { createTrackLayer(input: $input) { id tailWindow } }",
        {
            "input": {
                "scene": str(scene.id),
                "lens": await seed.whole_lens_id(models.TableDataset, str(dataset.id)),
            }
        },
    )
    layer = created["createTrackLayer"]
    assert layer["tailWindow"] is None, "the viewer chooses until someone does"

    update = "mutation U($input: UpdateTrackLayerInput!) { updateTrackLayer(input: $input) { id } }"
    await _ok(ctx, update, {"input": {"id": layer["id"], "tailWindow": 12}})
    reread = await _ok(
        ctx,
        "query Q($id: ID!) { layer(id: $id) { ... on TrackLayer { tailWindow } } }",
        {"id": layer["id"]},
    )
    assert reread["layer"]["tailWindow"] == 12

    await _ok(ctx, update, {"input": {"id": layer["id"], "tailWindow": 0}})
    reread = await _ok(
        ctx,
        "query Q($id: ID!) { layer(id: $id) { ... on TrackLayer { tailWindow } } }",
        {"id": layer["id"]},
    )
    assert reread["layer"]["tailWindow"] == 0, "zero is the whole track, not 'unset'"

    assert "greater than or equal to 0" in await _refused(
        ctx, update, {"input": {"id": layer["id"], "tailWindow": -1}}
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_chart_layer_keeps_its_value_range_until_autoscaled(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    _, lens, space = await _nuclei(ctx)
    chart = await _values_chart(ctx, space)
    layer = (
        await _ok(
            ctx,
            POINTS,
            {
                "input": {
                    "chart": chart["id"],
                    "lens": lens,
                    "xColumn": "area",
                    "yColumn": "volume",
                }
            },
        )
    )["createPointsChartLayer"]
    restyle = "mutation U($input: UpdateChartLayerInput!) { updateChartLayer(input: $input) { id valueMin valueMax } }"
    read = "query Q($id: ID!) { chartLayer(id: $id) { valueMin valueMax } }"

    await _ok(
        ctx, restyle, {"input": {"id": layer["id"], "valueMin": 2.5, "valueMax": 40}}
    )
    assert (await _ok(ctx, read, {"id": layer["id"]}))["chartLayer"] == {
        "valueMin": 2.5,
        "valueMax": 40.0,
    }

    await _ok(ctx, restyle, {"input": {"id": layer["id"], "opacity": 0.5}})
    assert (await _ok(ctx, read, {"id": layer["id"]}))["chartLayer"] == {
        "valueMin": 2.5,
        "valueMax": 40.0,
    }, "an unrelated restyle leaves the range"

    await _ok(ctx, restyle, {"input": {"id": layer["id"], "autoscale": True}})
    assert (await _ok(ctx, read, {"id": layer["id"]}))["chartLayer"] == {
        "valueMin": None,
        "valueMax": None,
    }

    assert "not below" in await _refused(
        ctx, restyle, {"input": {"id": layer["id"], "valueMin": 5, "valueMax": 5}}
    )
    assert "not both" in await _refused(
        ctx, restyle, {"input": {"id": layer["id"], "valueMin": 1, "autoscale": True}}
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colour_gradient_survives_the_render_graph(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    gradient = [
        {"position": 0.0, "color": [0, 0, 0, 255]},
        {"position": 0.4, "color": [255, 0, 0, 255]},
        {"position": 1.0, "color": [255, 255, 255, 255]},
    ]
    layer_id = await _create_layer_with_transfer(
        ctx, {"colormap": "VIRIDIS", "colorStops": gradient}
    )

    query = _STOPS_QUERY.replace(
        "stops { position value }",
        "stops { position value } colorStops { position color }",
    )
    result = await schema.execute(
        query, context_value=ctx, variable_values={"id": layer_id}
    )
    assert not result.errors, result.errors
    assert result.data
    assert (
        result.data["layer"]["renderGraph"]["root"]["children"][0]["transfer"][
            "colorStops"
        ]
        == gradient
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_colour_gradient_must_run_forwards_on_zero_to_one(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    for stops, said in (
        ([{"position": 0.0, "color": [0, 0, 0, 255]}], "at least two"),
        (
            [
                {"position": 0.6, "color": [0, 0, 0, 255]},
                {"position": 0.2, "color": [255, 255, 255, 255]},
            ],
            "backwards",
        ),
        (
            [
                {"position": 0.0, "color": [0, 0, 0, 255]},
                {"position": 1.5, "color": [255, 255, 255, 255]},
            ],
            "from 0 to 1",
        ),
    ):
        with pytest.raises(AssertionError, match=said):
            await _create_layer_with_transfer(
                ctx, {"colormap": "VIRIDIS", "colorStops": stops}
            )
