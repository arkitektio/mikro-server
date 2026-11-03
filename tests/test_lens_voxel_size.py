"""`ArrayLens.voxelSize`: one voxel size per lens, decided by the edges' validity.

The case that broke every client walker: a calibration written the way the CalibrateForm
writes it -- a BY_DIMENSION SCALE over (z, y, x) of a (c, z, y, x) grid -- carries its numbers
in its parts, and a reader of the edge-level `scale` found nothing.
"""

import pytest
from kante.context import HttpContext

from core import enums
from core.models import ArrayDataset
from mikro_server.schema import schema
from tests import seed

CREATE_CS = """
mutation CreateCS($input: CreateCoordinateSystemInput!) {
  createCoordinateSystem(input: $input) { id }
}
"""

LENS = """
query Lens($id: ID!) {
  lens(id: $id) {
    ... on ArrayLens {
      voxelSize { axis size unit type }
      voxelSizes { canonical validity hops problem system { id } axes { axis size } }
    }
  }
}
"""

STACK_AXES = [
    seed.axis("c", enums.AxisType.CHANNEL),
    seed.axis("z", enums.AxisType.SPACE),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]


def _micrometres(*names: str, time: str | None = None) -> list[dict]:
    axes = [{"name": time, "type": "TIME", "unit": "second"}] if time else []
    return axes + [{"name": name, "type": "SPACE", "unit": "micrometer"} for name in names]


async def _calibrate(ctx: HttpContext, dataset: ArrayDataset, scale: list[float], *, validity: str, axes: list[dict] | None = None, outputs=("z", "y", "x")) -> dict:
    registration = {
        "dataset": str(dataset.pk),
        "validity": validity,
        "transform": {"kind": "BY_DIMENSION", "parts": [{"kind": "SCALE", "inputAxes": ["z", "y", "x"], "outputAxes": list(outputs), "scale": scale}]},
    }
    return await schema.execute(
        CREATE_CS, context_value=ctx, variable_values={"input": {"name": f"{dataset.name} µm {validity}", "axes": axes or _micrometres("z", "y", "x"), "registrations": [registration]}}
    )


async def _lens(ctx: HttpContext, lens_id: str) -> dict:
    result = await schema.execute(LENS, context_value=ctx, variable_values={"id": lens_id})
    assert not result.errors, result.errors
    return result.data["lens"]


def _sizes(entries: list[dict]) -> dict[str, float]:
    return {entry["axis"]: round(entry["size"], 6) for entry in entries}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_canonical_voxel_size_follows_the_edges_validity(bot_context: HttpContext):
    dataset = await seed.create_array_dataset(bot_context, "Stack", axes=STACK_AXES, shapes=[[2, 8, 64, 64]])
    whole = await seed.whole_lens_id(ArrayDataset, dataset.pk)

    # Uncalibrated: nothing to answer, and nothing raised.
    assert await _lens(bot_context, whole) == {"voxelSize": [], "voxelSizes": []}

    # The converter's pixel size, read from the file: INFERRED, through BY_DIMENSION parts.
    result = await _calibrate(bot_context, dataset, [0.5, 0.25, 0.25], validity="INFERRED")
    assert not result.errors, result.errors
    lens = await _lens(bot_context, whole)
    assert _sizes(lens["voxelSize"]) == {"z": 0.5, "y": 0.25, "x": 0.25}  # c is in no unit: not listed
    assert {entry["type"] for entry in lens["voxelSize"]} == {"SPACE"}

    # A crop stepping x by 2 reaches the grid first, then the µm space: two hops, its own grid's size.
    crop = await seed.create_lens(bot_context, dataset, slices=[{"axis": "x", "start": 0, "stop": 32, "step": 2}])
    cropped = await _lens(bot_context, str(crop.pk))
    assert _sizes(cropped["voxelSize"]) == {"z": 0.5, "y": 0.25, "x": 0.5}
    assert [entry["hops"] for entry in cropped["voxelSizes"]] == [2]

    # A person's calibration afterwards: MANUAL outranks INFERRED, and the rival stays listed.
    result = await _calibrate(bot_context, dataset, [0.6, 0.25, 0.25], validity="MANUAL")
    assert not result.errors, result.errors
    lens = await _lens(bot_context, whole)
    assert _sizes(lens["voxelSize"]) == {"z": 0.6, "y": 0.25, "x": 0.25}
    assert [(entry["validity"], entry["canonical"]) for entry in lens["voxelSizes"]] == [("MANUAL", True), ("INFERRED", False)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_calibration_that_turns_space_into_time_is_refused(bot_context: HttpContext):
    dataset = await seed.create_array_dataset(bot_context, "Stack", axes=STACK_AXES, shapes=[[2, 8, 64, 64]])
    result = await _calibrate(bot_context, dataset, [0.5, 0.25, 0.25], validity="MANUAL", axes=_micrometres("y", "x", time="t"), outputs=("t", "y", "x"))
    # Refused where the part is written: a SCALE part puts axes in correspondence, and space is no time.
    assert result.errors and "'z' (SPACE) -> 't' (TIME)" in str(result.errors[0]), result.errors
