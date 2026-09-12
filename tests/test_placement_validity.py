"""Validity is an edge fact, and a layer derives it from its path.

`Layer.validity` used to be a stored column: a per-layer copy of how-known one
*registration* is, which two layers over one dataset were free to disagree about --
and which nothing ever wrote, so every layer said UNKNOWN forever. It now lives on
the transformation edge, where the writers actually know it (derived plumbing is
VALIDATED, a calibration is INFERRED, an authored registration is MANUAL, and a
client that knows it is guessing says UNKNOWN), and the layer's validity is *derived*: the weakest edge
on its path to world. Fixing one edge fixes every layer that looks through it,
because there is only one copy of the fact.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from core import enums, models
from mikro_server.schema import schema
from tests import seed


def _fresh_request(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity.

    The scene-graph memo lives on the request context, so reusing one across two
    executions would let the second read the edges as they were before the mutation --
    exactly the staleness a real client, with one context per request, never sees.
    """
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")

LAYER_VALIDITY = """
query LayerValidity($id: ID!) {
  scene(id: $id) {
    layers { id placementValidity pathToWorld { transformation { id validity } } }
  }
}
"""

REGISTER = """
mutation Register($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id validity }
}
"""

REFINE = """
mutation Refine($input: UpdateTransformationInput!) {
  updateTransformation(input: $input) { id validity }
}
"""


async def _layer_validity(ctx: HttpContext, scene_id: str) -> str:
    result = await schema.execute(LAYER_VALIDITY, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer["placementValidity"]


async def _layer_path(ctx: HttpContext, scene_id: str) -> list:
    """The layer's edges, so a test asserting on a *minimum* can prove there is one to take."""
    result = await schema.execute(LAYER_VALIDITY, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer["pathToWorld"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_assumed_placement_reads_unknown(authenticated_context: HttpContext):
    """An edge a client admits it guessed reads UNKNOWN, and the layer surfaces it.

    The server writes UNKNOWN nowhere: it fabricates no placements, so the badge exists for
    a client that has one and knows it is a guess -- an alignment eyeballed from a montage,
    a stage position read off a filename. It is authored like any other edge; only the
    validity differs.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Assumed", shapes=[[2, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": str(world.pk),
                "validity": "UNKNOWN",
                "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"], "scale": [1.0, 1.0]},
            }
        },
    )
    assert not registered.errors, registered.errors
    assert registered.data["createTransformation"]["validity"] == "UNKNOWN"

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert not created.errors, created.errors

    assert await _layer_validity(authenticated_context, str(scene.pk)) == "UNKNOWN"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_authored_registration_reads_manual_and_validating_it_needs_no_layer_write(authenticated_context: HttpContext):
    """MANUAL when someone authors the edge; VALIDATED the moment the edge says so.

    The second half is the point of the move: validating a registration touches the
    *edge*, and the layer -- every layer over this dataset -- reflects it immediately,
    because its validity is derived, never stored.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Registered")  # (c, y, x)
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Composition")  # (z, y, x)
    intrinsic, world = await sync_to_async(lambda: (dataset.intrinsic_coordinate_system, scene.world))()

    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "input": str(intrinsic.pk),
                "output": str(world.pk),
                "transform": {
                    "kind": "BY_DIMENSION",
                    "inputAxes": ["y", "x"],
                    "outputAxes": ["y", "x"],
                    "affine": [[1.0, 0.0, 10.0], [0.0, 1.0, 20.0]],
                },
            }
        },
    )
    assert not registered.errors, registered.errors
    assert registered.data["createTransformation"]["validity"] == "MANUAL", "an edge that arrived through the API was authored by someone"
    edge_id = registered.data["createTransformation"]["id"]

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk), "intensityAxis": "c"}},
    )
    assert not created.errors, created.errors

    assert await _layer_validity(authenticated_context, str(scene.pk)) == "MANUAL"

    refined = await schema.execute(REFINE, context_value=authenticated_context, variable_values={"input": {"id": edge_id, "validity": "VALIDATED"}})
    assert not refined.errors, refined.errors
    assert refined.data["updateTransformation"]["validity"] == "VALIDATED"

    assert await _layer_validity(authenticated_context, str(scene.pk)) == "VALIDATED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_weakest_edge_on_the_path_wins(authenticated_context: HttpContext):
    """Two hops, two validities: the layer reads the weaker one, and fixing it lifts the layer.

    A **sliced** lens in a scene over the dataset's physical space walks two edges: the crop
    into the pixel grid (VALIDATED -- the server derived it from the slices) and the
    calibration into the physical space (INFERRED -- someone read a pixel size off metadata).
    The layer reads INFERRED, because a placement is only as right as its weakest claim.

    The second hop is what makes this a test rather than a tautology: over a one-edge path
    the minimum is the edge itself and nothing is being asserted.

    Validating the calibration -- one edge write, no layer write -- lifts the layer, because
    its validity is derived.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Calibrated", shapes=[[2, 64, 64]])
    calibration = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[
            seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."),
            seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
        ],
        scale=[1.0, 0.325, 0.325],
    )
    sliced = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])

    scene = await schema.execute(
        "mutation S($input: CreateSceneInput!) { createScene(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"name": "Physical", "coordinateSystem": str(calibration.pk)}},
    )
    assert not scene.errors, scene.errors
    scene_id = scene.data["createScene"]["id"]

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": scene_id, "lens": str(sliced.pk), "intensityAxis": "c"}},
    )
    assert not created.errors, created.errors

    hops = await _layer_path(authenticated_context, scene_id)
    assert [hop["transformation"]["validity"] for hop in hops] == ["VALIDATED", "INFERRED"], (
        f"the path must have two hops of differing validity or the minimum below asserts nothing: {hops}"
    )
    assert await _layer_validity(authenticated_context, scene_id) == "INFERRED", "the calibration is the weakest claim on the path"

    def validate_calibration() -> None:
        edge = models.Transformation.objects.get(output=calibration)
        edge.validity = "VALIDATED"
        edge.save(update_fields=["validity"])

    await sync_to_async(validate_calibration)()

    assert await _layer_validity(authenticated_context, scene_id) == "VALIDATED", "fixing the one edge fixes every layer that looks through it"


def test_the_layer_carries_no_placement_columns():
    """`status` had no readers and the layer's validity is derived: neither is a stored Layer column, and the derived field wears its own name."""
    sdl = schema.as_str()
    definition = sdl[sdl.find("interface Layer ") : sdl.find("\n}", sdl.find("interface Layer "))]
    assert "\n  status" not in definition
    assert "placementValidity(" in definition and "): PlacementValidity!" in definition, "the derived aggregate survives, under its own name and taking the coordinate to answer at"
    assert "\n  validity" not in definition, "the bare word belongs to the edge, not the layer"

    transformation = sdl[sdl.find("interface Transformation ") : sdl.find("\n}", sdl.find("interface Transformation "))]
    assert "validity: PlacementValidity" in transformation, "the stored fact lives on the edge"
