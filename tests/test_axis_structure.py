"""Axes carry structure; what reads them checks it.

A coordinate system assumes nothing. Each axis is *measured* (SPACE, TIME, MICROTIME, SPECTRUM)
or *enumerating* (CHANNEL, INDEX, CATEGORY), and may declare a **metric group**: the axes that
share one distance with it. A SPACE axis is in ``"space"`` unless it says otherwise. A rotation
needs the axes it mixes to share a group; a per-axis scale, an offset and an affine map do not.
The pins below are the write-time halves of that: a CATEGORY never on a grid and never with a
unit, groups only on measured axes of one dimension, rotations only within a group, arithmetic
never on a category, and a refined rotation stays one.
"""

import math

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id axes { name type unit metricGroup } }
}
"""

CREATE_TRANSFORM = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id kind }
}
"""

UPDATE_TRANSFORM = """
mutation Update($input: UpdateTransformationInput!) {
  updateTransformation(input: $input) { id version }
}
"""

COS, SIN = math.cos(0.3), math.sin(0.3)
TURN_2D = [[COS, -SIN, 0.0], [SIN, COS, 0.0]]


async def _space(ctx: HttpContext, name: str, axes: list[dict]):
    return await schema.execute(CREATE_CS, context_value=ctx, variable_values={"input": {"name": name, "axes": axes}})


async def _space_id(ctx: HttpContext, name: str, axes: list[dict]) -> str:
    result = await _space(ctx, name, axes)
    assert not result.errors, result.errors
    return result.data["createCoordinateSystem"]["id"]


async def _edge(ctx: HttpContext, input_id: str, output_id: str, transform: dict):
    return await schema.execute(CREATE_TRANSFORM, context_value=ctx, variable_values={"input": {"input": input_id, "output": output_id, "transform": transform}})


def _spectrum(name: str, group: str | None = None, unit: str = "nanometer") -> dict:
    axis: dict = {"name": name, "type": "SPECTRUM", "unit": unit}
    if group is not None:
        axis["metricGroup"] = group
    return axis


#: Two wavelengths that declare no group: both measured, both lengths, and no angle between them.
MIXED = [_spectrum("w1"), _spectrum("w2")]


# --- units and categories -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_unitless_measured_axis_is_refused(authenticated_context: HttpContext):
    """A unitless SPACE axis in a unit-carrying space is a pixel grid in disguise."""
    result = await _space(authenticated_context, "bad", [{"name": "x", "type": "SPACE"}])
    assert result.errors
    assert "has no unit" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_category_has_no_unit(authenticated_context: HttpContext):
    ok = await _space(authenticated_context, "conditions", [{"name": "condition", "type": "CATEGORY"}, _spectrum("w")])
    assert not ok.errors, ok.errors

    refused = await _space(authenticated_context, "bad", [{"name": "condition", "type": "CATEGORY", "unit": "second"}])
    assert refused.errors
    assert "CATEGORY axis" in str(refused.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_grid_has_no_category_axis(authenticated_context: HttpContext):
    """An array is indexed by position; a category is positioned by value."""
    with pytest.raises(ValueError, match="CATEGORY axis"):
        await seed.create_array_dataset(authenticated_context, "Conditions", axes=[seed.axis("i", enums.AxisType.INDEX), seed.axis("condition", enums.AxisType.CATEGORY)], shapes=[[4, 2]])


# --- metric groups --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_space_axis_is_in_the_space_group_by_default(authenticated_context: HttpContext):
    result = await _space(authenticated_context, "stage", [{"name": "t", "type": "TIME", "unit": "second"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}])
    assert not result.errors, result.errors
    groups = {axis["name"]: axis["metricGroup"] for axis in result.data["createCoordinateSystem"]["axes"]}
    assert groups == {"t": None, "y": "space", "x": "space"}

    system = await sync_to_async(models.CoordinateSystem.objects.get)(pk=result.data["createCoordinateSystem"]["id"])
    stored = await sync_to_async(lambda: list(system.axes.values_list("metric_group", flat=True)))()
    assert stored == [None, None, None], "the default lives in code, so a space from before groups needs no migration"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_enumerating_axis_cannot_declare_a_group(authenticated_context: HttpContext):
    result = await _space(authenticated_context, "bad", [{"name": "condition", "type": "CATEGORY", "metricGroup": "z"}, _spectrum("w", "z")])
    assert result.errors
    assert "no distance to share" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_group_measures_one_dimension(authenticated_context: HttpContext):
    """A wavelength beside a duration is not one distance; two wavelengths of one detector are."""
    mixed = await _space(authenticated_context, "bad", [_spectrum("w", "f"), {"name": "t", "type": "TIME", "unit": "second", "metricGroup": "f"}])
    assert mixed.errors
    assert "measure different things" in str(mixed.errors[0])

    same = await _space(authenticated_context, "detector", [_spectrum("w1", "detector"), _spectrum("w2", "detector")])
    assert not same.errors, same.errors


# --- rotations need one distance ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rotation_part_mixing_ungrouped_axes_is_refused(authenticated_context: HttpContext):
    """Both are lengths, and still share no distance until they say so: no angle between them exists to rotate by."""
    a = await _space_id(authenticated_context, "a", MIXED)
    b = await _space_id(authenticated_context, "b", MIXED)

    result = await _edge(authenticated_context, a, b, {"kind": "BY_DIMENSION", "parts": [{"kind": "ROTATION", "inputAxes": ["w1", "w2"], "outputAxes": ["w1", "w2"], "affine": TURN_2D}]})
    assert result.errors
    message = str(result.errors[0])
    assert "share a metric group" in message and "'w1' (no metric group)" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rotation_within_a_declared_group_is_written(authenticated_context: HttpContext):
    """Two wavelengths declared one group share a distance, so turning between them is a rotation."""
    a = await _space_id(authenticated_context, "a", [_spectrum("w1", "detector"), _spectrum("w2", "detector")])
    b = await _space_id(authenticated_context, "b", [_spectrum("w1", "detector"), _spectrum("w2", "detector")])

    result = await _edge(authenticated_context, a, b, {"kind": "BY_DIMENSION", "parts": [{"kind": "ROTATION", "inputAxes": ["w1", "w2"], "outputAxes": ["w1", "w2"], "affine": TURN_2D}]})
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_part_mixing_ungrouped_axes_is_written(authenticated_context: HttpContext):
    """An affine map claims no lengths or angles, so it may mix what a rotation may not."""
    a = await _space_id(authenticated_context, "a", MIXED)
    b = await _space_id(authenticated_context, "b", MIXED)

    result = await _edge(authenticated_context, a, b, {"kind": "BY_DIMENSION", "parts": [{"kind": "AFFINE", "inputAxes": ["w1", "w2"], "outputAxes": ["w1", "w2"], "affine": [[0.2, 0.7, -1.0], [-0.7, 0.2, 3.0]]}]})
    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_rotation_mixing_time_and_space_is_refused(authenticated_context: HttpContext):
    """The gap the rule closes: a ROTATION turning t into x was accepted, and reported an isometry."""
    a = await _space_id(authenticated_context, "a", [{"name": "t", "type": "TIME", "unit": "second"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}])
    b = await _space_id(authenticated_context, "b", [{"name": "t", "type": "TIME", "unit": "second"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}])

    result = await _edge(authenticated_context, a, b, {"kind": "ROTATION", "affine": TURN_2D})
    assert result.errors
    assert "share a metric group" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_rotation_passing_time_through_is_written(authenticated_context: HttpContext):
    """Only the axes a rotation mixes need the distance: t passed through needs nothing."""
    axes = [{"name": "t", "type": "TIME", "unit": "second"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]
    a = await _space_id(authenticated_context, "a", axes)
    b = await _space_id(authenticated_context, "b", axes)

    result = await _edge(authenticated_context, a, b, {"kind": "ROTATION", "affine": [[1.0, 0.0, 0.0, 0.0], [0.0, COS, -SIN, 0.0], [0.0, SIN, COS, 0.0]]})
    assert not result.errors, result.errors


# --- categories take no arithmetic ----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_metric_edge_over_a_space_with_a_category_is_refused(authenticated_context: HttpContext):
    axes = [{"name": "condition", "type": "CATEGORY"}, _spectrum("w")]
    a = await _space_id(authenticated_context, "a", axes)
    b = await _space_id(authenticated_context, "b", axes)

    result = await _edge(authenticated_context, a, b, {"kind": "SCALE", "scale": [1.0, 2.0]})
    assert result.errors
    assert "CATEGORY axis" in str(result.errors[0]) and "IDENTITY part" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_parts_pass_a_category_through_and_scale_the_rest(authenticated_context: HttpContext):
    a = await _space_id(authenticated_context, "a", [{"name": "condition", "type": "CATEGORY"}, _spectrum("w")])
    b = await _space_id(authenticated_context, "b", [{"name": "condition", "type": "CATEGORY"}, _spectrum("w2")])

    ok = await _edge(
        authenticated_context,
        a,
        b,
        {
            "kind": "BY_DIMENSION",
            "parts": [
                {"kind": "IDENTITY", "inputAxes": ["condition"], "outputAxes": ["condition"]},
                {"kind": "AFFINE", "inputAxes": ["w"], "outputAxes": ["w2"], "affine": [[0.5, -2.0]]},
            ],
        },
    )
    assert not ok.errors, ok.errors

    refused = await _edge(authenticated_context, a, b, {"kind": "BY_DIMENSION", "parts": [{"kind": "SCALE", "inputAxes": ["condition"], "outputAxes": ["condition"], "scale": [2.0]}]})
    assert refused.errors
    assert "no number to compute with" in str(refused.errors[0])


# --- a refined rotation stays one -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rotation_cannot_be_refined_into_a_shear(authenticated_context: HttpContext):
    """Orthonormality was checked only by the creation inputs, so a refinement could break it."""
    axes = [{"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]
    a = await _space_id(authenticated_context, "a", axes)
    b = await _space_id(authenticated_context, "b", axes)
    created = await _edge(authenticated_context, a, b, {"kind": "ROTATION", "affine": TURN_2D})
    assert not created.errors, created.errors

    refined = await schema.execute(UPDATE_TRANSFORM, context_value=authenticated_context, variable_values={"input": {"id": created.data["createTransformation"]["id"], "affine": [[1.0, 0.5, 0.0], [0.0, 1.0, 0.0]]}})
    assert refined.errors
    assert "orthonormal" in str(refined.errors[0]).lower() or "rotation" in str(refined.errors[0]).lower()
