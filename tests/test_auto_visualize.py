"""autoVisualize: a space is shown as whichever composition its axes call for.

A scene and a chart are both visualizations of a space. These tests pin the choice between
them -- one metric, unit-carrying axis is a chart, any other navigable space is a scene, and
a space that is neither is refused -- and that the two are read through one interface, and
listed together by the space they are laid out over.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from chart import models as chart_models
from core import enums, models
from mikro_server.schema import schema
from tests import seed

AUTO = """
mutation Auto($input: AutoVisualizeInput!) {
  autoVisualize(input: $input) {
    __typename
    id
    name
    descriptors
    worldCoordinateSystem { id }
    ... on Chart { axis { name } layers { __typename name } }
    ... on Scene { layers { __typename } }
  }
}
"""

CREATE_SYSTEM = "mutation M($input: CreateCoordinateSystemInput!) { createCoordinateSystem(input: $input) { id } }"
REGISTER = "mutation R($input: CreateTransformationInput!) { createTransformation(input: $input) { id } }"
CREATE_SCENE = "mutation S($input: CreateSceneInput!) { createScene(input: $input) { id } }"

VISUALIZATIONS = """
query Space($id: ID!) {
  coordinateSystem(id: $id) { visualizations { __typename id name worldCoordinateSystem { id } } }
}
"""

AS_VISUALIZATION = """
query Both($scene: ID!, $chart: ID!) {
  scene(id: $scene) { ...Shown }
  chart(id: $chart) { ...Shown }
}
fragment Shown on Visualization { __typename id name descriptors worldCoordinateSystem { id } }
"""

_MICROMETRES = {"name": "d", "type": "SPACE", "unit": "micrometer"}


def _fresh(ctx: HttpContext) -> HttpContext:
    """A new request for the same identity: the graph memos live on the context."""
    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=ctx.request._client,
        _user=ctx.request._user,
        _organization=ctx.request._organization,
    )
    request.set_membership(ctx.request._membership)  # type: ignore[arg-type]
    return HttpContext(request=request, response=TemporalResponse(), headers=ctx.headers, type="http")


async def _run(ctx: HttpContext, document: str, input: dict | None = None, **variables: object):
    if input is not None:
        variables["input"] = input
    return await schema.execute(document, context_value=_fresh(ctx), variable_values=variables or None)


async def _ok(ctx: HttpContext, document: str, input: dict | None = None, **variables: object) -> dict:
    result = await _run(ctx, document, input, **variables)
    assert not result.errors, result.errors
    return result.data


async def _space(ctx: HttpContext, name: str, axes: list[dict]) -> str:
    return (await _ok(ctx, CREATE_SYSTEM, {"name": name, "axes": axes}))["createCoordinateSystem"]["id"]


async def _auto(ctx: HttpContext, system: object, name: str | None = None) -> dict:
    return (await _ok(ctx, AUTO, {"coordinateSystem": str(system), "name": name}))["autoVisualize"]


async def _compositions() -> tuple[int, int]:
    return await sync_to_async(models.Scene.objects.count)(), await sync_to_async(chart_models.Chart.objects.count)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_with_one_metric_axis_is_charted_from_what_is_laid_along_it(authenticated_context: HttpContext):
    """One micrometre axis and a profile registered onto it: a chart, with the profile as its trace."""
    ctx = authenticated_context
    world = await _space(ctx, "distance", [_MICROMETRES])
    profile = await seed.create_array_dataset(ctx, "Profile", axes=[seed.axis("d", enums.AxisType.SPACE)], shapes=[[64]])
    await _ok(ctx, REGISTER, {"input": str(profile.coordinate_system_id), "output": world, "transform": {"kind": "BY_DIMENSION", "inputAxes": ["d"], "outputAxes": ["d"], "affine": [[0.25, 0.0]]}})
    edges = await sync_to_async(models.Transformation.objects.count)()

    shown = await _auto(ctx, world)

    assert shown["__typename"] == "Chart"
    assert shown["name"] == "distance", "unnamed, it takes the space's name"
    assert shown["worldCoordinateSystem"]["id"] == world
    assert shown["axis"] == {"name": "d"}
    assert [(layer["__typename"], layer["name"]) for layer in shown["layers"]] == [("TraceChartLayer", "Profile")]
    assert await sync_to_async(models.Transformation.objects.count)() == edges, "showing a space authors no edges"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "axis",
    [
        {"name": "t", "type": "TIME", "unit": "second"},
        {"name": "arrival", "type": "MICROTIME", "unit": "nanosecond"},
        {"name": "wavelength", "type": "SPECTRUM", "unit": "nanometer"},
    ],
    ids=["time", "microtime", "spectrum"],
)
async def test_any_single_metric_axis_is_a_chart_even_one_a_scene_would_accept(authenticated_context: HttpContext, axis: dict):
    """A lone time axis could carry a scene too; it is read as an axis before it is read as a place."""
    shown = await _auto(authenticated_context, await _space(authenticated_context, "axis", [axis]))
    assert shown["__typename"] == "Chart"
    assert shown["layers"] == [], "nothing resides here, and an empty chart is still the answer"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_s_own_pixel_grid_is_staged_as_a_scene_of_its_data(authenticated_context: HttpContext):
    """A unitless grid is no axis to read values off. The image living in it becomes the layers."""
    ctx = authenticated_context
    image = await seed.create_array_dataset(ctx, "Cells")  # (c, y, x)

    shown = await _auto(ctx, image.coordinate_system_id, name="Cells, staged")

    assert shown["__typename"] == "Scene"
    assert shown["name"] == "Cells, staged"
    assert shown["worldCoordinateSystem"]["id"] == str(image.coordinate_system_id)
    assert shown["layers"], "the resident image is what the scene shows"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shared_space_with_several_axes_is_a_scene_of_what_is_registered_into_it(authenticated_context: HttpContext):
    """Two micrometre axes are a place: the image registered into it is drawn there."""
    ctx = authenticated_context
    atlas = await _space(ctx, "atlas", [{"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}])
    image = await seed.create_array_dataset(ctx, "Section", axes=seed.YX_AXES, shapes=[[64, 64]])
    await _ok(ctx, REGISTER, {"input": str(image.coordinate_system_id), "output": atlas, "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}})

    shown = await _auto(ctx, atlas)

    assert shown["__typename"] == "Scene"
    assert len(shown["layers"]) == 1

    # And with nothing registered, the scene is empty rather than refused.
    empty = await _auto(ctx, await _space(ctx, "bare", [{"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]))
    assert (empty["__typename"], empty["layers"]) == ("Scene", [])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_neither_composition_can_show_is_refused_and_nothing_is_made(authenticated_context: HttpContext):
    """Two enumerating axes: more than a chart's one, and no time or space to navigate."""
    ctx = authenticated_context
    system = await _space(ctx, "labels", [{"name": "c", "type": "CHANNEL", "unit": "a.u."}, {"name": "object", "type": "INDEX", "unit": "a.u."}])
    before = await _compositions()

    refused = await _run(ctx, AUTO, {"coordinateSystem": system})

    assert refused.errors and "neither as a chart nor as a scene" in str(refused.errors[0]), refused.errors
    assert await _compositions() == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_lists_its_scenes_and_charts_together_as_visualizations(authenticated_context: HttpContext, other_org_context: HttpContext):
    """One space, a scene made by hand and a chart made automatically: both are over it, and it says so."""
    ctx = authenticated_context
    world = await _space(ctx, "distance", [_MICROMETRES])
    scene_id = (await _ok(ctx, CREATE_SCENE, {"name": "By hand", "coordinateSystem": world}))["createScene"]["id"]
    chart = await _auto(ctx, world, name="Automatic")

    listed = (await _ok(ctx, VISUALIZATIONS, id=world))["coordinateSystem"]["visualizations"]
    assert [(shown["__typename"], shown["id"], shown["name"]) for shown in listed] == [("Scene", scene_id, "By hand"), ("Chart", chart["id"], "Automatic")]
    assert {shown["worldCoordinateSystem"]["id"] for shown in listed} == {world}

    # Either is read through the one interface, by a fragment that names neither.
    both = await _ok(ctx, AS_VISUALIZATION, scene=scene_id, chart=chart["id"])
    assert both["scene"]["__typename"] == "Scene" and both["chart"]["__typename"] == "Chart"
    assert both["chart"]["descriptors"] == {"@mikro/axis_type": "SPACE", "@mikro/axis_unit": "micrometer"}
    assert "@mikro/blending" in both["scene"]["descriptors"]

    # Another organization cannot show a space it cannot see.
    before = await _compositions()
    foreign = await _run(other_org_context, AUTO, {"coordinateSystem": world})
    assert foreign.errors, "the space belongs to another organization"
    assert await _compositions() == before
