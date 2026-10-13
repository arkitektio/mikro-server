"""The crop edge is true: lens voxel (i, j) is dataset voxel (start + i·step, start + j·step), everywhere it is read.

Every case is checked against numpy, not against the server's own arithmetic: an array of
distinct values is sliced the way the lens says, and every map the server hands out -- the
stored edge, the composed path to a world, the inverted edge, the path under a calibration,
the box of an annotation drawn on the crop -- must land each lens voxel on the dataset voxel
holding the same value. The server never reads pixels, so this is the oracle.
"""

import math
from io import StringIO

import numpy as np
import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from kante.context import HttpContext

from core import enums, models
from core.logic import coords, space_graph
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import AS_AFFINE, _apply, _fresh_request
from tests.test_layer_kinds import _seed_table_dataset
from tests.test_merge_duplicate_lenses import _legacy_sliced_lens

EDGE = """
fragment Edge on Transformation {
  id kind inputAxes outputAxes
  ... on ScaleTransformation { scale }
  ... on TranslationTransformation { translation }
}
"""

CREATE_LENS = (
    EDGE
    + """
mutation CreateLens($input: CreateLensInput!) {
  createLens(input: $input) {
    id shape axisNames
    coordinateSystem { id }
    toParent { ...Edge ... on SequenceTransformation { steps { kind scale translation } } }
  }
}
"""
)

CREATE_SCENE = "mutation CreateScene($input: CreateSceneInput!) { createScene(input: $input) { id worldCoordinateSystem { id axes { name } } } }"
CREATE_INTENSITY = "mutation Make($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }"
CREATE_COLLECTION = "mutation CreateCollection($input: CreateAnnotationCollectionInput!) { createAnnotationCollection(input: $input) { id coordinateSystem { id } } }"
CREATE_ANNOTATION = "mutation Create($input: CreateAnnotationInput!) { createAnnotation(input: $input) { id intrinsicBbox { min max } } }"
OVERLAPPING = "query Overlapping($annotation: ID!) { lenses(filters: { overlapsAnnotation: $annotation }) { id } }"
LEVEL_PATHS = """
query LevelPaths($id: ID!) {
  scene(id: $id) {
    layers {
      pathToWorld { inverted transformation { kind input { id } output { id } } }
      ... on IntensityLayer { levelPaths { dataArray { level } path { inverted transformation { kind input { id } output { id } } } } }
    }
  }
}
"""

#: A dataset small enough to check every voxel of every crop.
SHAPE = [3, 6, 8]


# --- the oracle --------------------------------------------------------------------


def _oracle(shape: list[int], axis_names: list[str], slices: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """The array of distinct values and the view the slices cut out of it, by numpy's own rules."""
    arr = np.arange(math.prod(shape)).reshape(shape)
    by_axis = {entry["axis"]: entry for entry in slices}
    index = tuple(slice(by_axis[axis].get("start"), by_axis[axis].get("stop"), by_axis[axis].get("step")) if axis in by_axis else slice(None) for axis in axis_names)
    return arr, arr[index]


def _assert_maps_every_voxel(matrix: list[list[float]], arr: np.ndarray, view: np.ndarray) -> None:
    """Every lens voxel, pushed through `matrix`, lands on the dataset voxel holding its value."""
    assert view.size, "an empty view proves nothing"
    for index in np.ndindex(*view.shape):
        landed = _apply(matrix, [float(i) for i in index])
        parent = tuple(int(round(value)) for value in landed)
        assert all(abs(value - round(value)) < 1e-9 for value in landed), f"lens voxel {index} lands between voxels at {landed}"
        assert all(value >= 0 for value in parent), f"lens voxel {index} lands outside the array at {parent} (numpy would alias a negative index onto the right voxel, the server would not)"
        assert view[index] == arr[parent], f"lens voxel {index} maps to dataset voxel {parent}, which holds {arr[parent]} rather than {view[index]}"


def _edge_params(edge: dict) -> list[tuple[str, dict]]:
    """A fetched `toParent` as the (kind, params) steps `coords.compose` takes."""
    if "steps" in edge:
        steps = edge["steps"]
        scale = next(step["scale"] for step in steps if step["scale"])
        translation = next(step["translation"] for step in steps if step["translation"])
        return [(enums.TransformKindChoices.SEQUENCE.value, {"scale": scale, "translation": translation})]
    return [(enums.TransformKindChoices.TRANSLATION.value, {"translation": edge["translation"]})]


def _matrix(edge: dict, rank: int) -> list[list[float]]:
    """The fetched edge as an M x (N+1) matrix, rows per output axis, last column the translation."""
    square = coords.compose(_edge_params(edge), rank)
    return [row for row in square[:-1]]


# --- GraphQL helpers -----------------------------------------------------------------


async def _lens(ctx: HttpContext, dataset: models.ArrayDataset, slices: list[dict]) -> dict:
    result = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"dataset": str(dataset.pk), "slices": slices}})
    assert not result.errors, result.errors
    return result.data["createLens"]


async def _scene_over(ctx: HttpContext, system_id: str, name: str = "Scene") -> dict:
    result = await schema.execute(CREATE_SCENE, context_value=_fresh_request(ctx), variable_values={"input": {"name": name, "coordinateSystem": system_id}})
    assert not result.errors, result.errors
    return result.data["createScene"]


async def _intensity_layer(ctx: HttpContext, scene_id: str, lens_id: str) -> dict:
    """An intensity layer over channel 0 of the lens, and the channel lens it draws through.

    A layer draws one channel, so the lens it reads is the channel cut from the one given --
    the dataset's lens for the composed selection, with its own edge straight into the grid.
    Channel 0 adds no offset, so the geometry the oracle checks is the given lens'.
    """
    channel = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": lens_id, "slices": [{"axis": "c", "start": 0, "stop": 1}]}})
    assert not channel.errors, channel.errors
    result = await schema.execute(CREATE_INTENSITY, context_value=_fresh_request(ctx), variable_values={"input": {"scene": scene_id, "lens": channel.data["createLens"]["id"]}})
    assert not result.errors, result.errors
    return channel.data["createLens"]


async def _only_layer(ctx: HttpContext, scene_id: str, query: str = AS_AFFINE) -> dict:
    result = await schema.execute(query, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer


async def _collection_on(ctx: HttpContext, *, axes: list[str], derived_from: dict, name: str = "Drawn") -> dict:
    result = await schema.execute(
        CREATE_COLLECTION,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"name": name, "axes": [{"name": axis, "type": "SPACE"} for axis in axes], "derivedFrom": [derived_from]}},
    )
    assert not result.errors, result.errors
    return result.data["createAnnotationCollection"]


async def _draw(ctx: HttpContext, collection_id: str, kind: str, vectors: list[list[float]]) -> dict:
    result = await schema.execute(CREATE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"collection": collection_id, "kind": kind, "vectors": vectors}})
    assert not result.errors, result.errors
    return result.data["createAnnotation"]


async def _overlapping(ctx: HttpContext, annotation_id: str) -> set[str]:
    result = await schema.execute(OVERLAPPING, context_value=_fresh_request(ctx), variable_values={"annotation": annotation_id})
    assert not result.errors, result.errors
    return {lens["id"] for lens in result.data["lenses"]}


# --- 1. the stored edge ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slices",
    [
        [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}],
        [{"axis": "y", "start": -5}, {"axis": "x", "start": 2, "stop": 700}],
        [{"axis": "c", "start": 1}, {"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}],
        [{"axis": "x", "start": 2, "stop": 7}, {"axis": "c", "start": 1, "stop": 3}],
        [{"axis": "y", "start": 0, "stop": 6, "step": 2}, {"axis": "x", "start": 1, "stop": 8, "step": 3}],
        [{"axis": "c", "start": 2}, {"axis": "y", "start": 1, "step": 2}, {"axis": "x", "start": -5, "step": 2}],
    ],
    ids=["crop-yx", "negative-and-open", "crop-cyx", "out-of-order", "stepped-yx", "stepped-open"],
)
async def test_the_stored_edge_maps_every_lens_voxel_onto_its_dataset_voxel(authenticated_context: HttpContext, slices: list[dict]):
    """`toParent`, composed, is numpy's own slicing: every lens voxel lands on the dataset voxel with its value, and the shape is the view's."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Small", shapes=[SHAPE])
    arr, view = _oracle(SHAPE, ["c", "y", "x"], slices)

    lens = await _lens(ctx, dataset, slices)

    assert lens["shape"] == list(view.shape)
    assert lens["toParent"]["inputAxes"] == lens["toParent"]["outputAxes"] == ["c", "y", "x"]
    _assert_maps_every_voxel(_matrix(lens["toParent"], 3), arr, view)


# --- 2-4. through the scene graph ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slices",
    [
        [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}],
        [{"axis": "y", "start": 0, "stop": 6, "step": 2}, {"axis": "x", "start": 1, "stop": 8, "step": 3}],
    ],
    ids=["crop", "stepped"],
)
async def test_a_cropped_layer_in_the_datasets_own_grid_composes_the_crop_forward(authenticated_context: HttpContext, slices: list[dict]):
    """A scene rooted at intrinsic: the layer's one hop is the lens edge, forward, and `asAffine` is the oracle's map."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Small", shapes=[SHAPE])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    arr, view = _oracle(SHAPE, ["c", "y", "x"], slices)
    lens = await _lens(ctx, dataset, slices)

    scene = await _scene_over(ctx, str(intrinsic.pk))
    channel = await _intensity_layer(ctx, scene["id"], lens["id"])
    layer = await _only_layer(ctx, scene["id"])

    assert layer["placement"] == "PLACED"
    assert [(step["inverted"], step["transformation"]["id"]) for step in layer["pathToWorld"]] == [(False, channel["toParent"]["id"])], "one hop: the channel lens' own edge, which composes the crop and the channel into the grid"
    assert layer["asAffine"]["inputAxes"] == layer["asAffine"]["outputAxes"] == ["c", "y", "x"]
    assert layer["asAffine"]["total"] is True
    _assert_maps_every_voxel(layer["asAffine"]["matrix"], arr, view)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scene_rooted_at_the_crop_inverts_the_edge_for_the_whole_dataset(authenticated_context: HttpContext):
    """The lens system as world, the whole dataset as a layer: the lens edge is walked backwards, and dataset voxel q lands on lens voxel q - start."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Small", shapes=[SHAPE])
    slices = [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}]
    arr, view = _oracle(SHAPE, ["c", "y", "x"], slices)
    crop = await _lens(ctx, dataset, slices)
    whole = await _lens(ctx, dataset, [])

    scene = await _scene_over(ctx, crop["coordinateSystem"]["id"], name="Crop view")
    channel = await _intensity_layer(ctx, scene["id"], whole["id"])
    layer = await _only_layer(ctx, scene["id"])

    assert [(step["inverted"], step["transformation"]["id"]) for step in layer["pathToWorld"]] == [(False, channel["toParent"]["id"]), (True, crop["toParent"]["id"])], "into the grid through the channel's own edge, then the crop's edge walked backwards"
    matrix = layer["asAffine"]["matrix"]
    # The inverse map: every dataset voxel inside the crop lands on the lens voxel holding its value.
    for c in range(SHAPE[0]):
        for y in range(1, 5):
            for x in range(2, 7):
                landed = tuple(int(round(value)) for value in _apply(matrix, [float(c), float(y), float(x)]))
                assert landed == (c, y - 1, x - 2)
                assert view[landed] == arr[c, y, x]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_under_a_calibration_is_the_calibration_of_the_shifted_voxel(authenticated_context: HttpContext):
    """Intrinsic calibrated into micrometres by an affine: the cropped layer's map is `calibration ∘ (p + start)`, to the last digit."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Small", shapes=[SHAPE])
    slices = [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}]
    arr, view = _oracle(SHAPE, ["c", "y", "x"], slices)
    calibration = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.5, 0.0, 100.0], [0.0, 0.0, 0.25, -3.0]]
    physical = await seed.create_physical_space(
        ctx,
        dataset,
        axes=[seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."), seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        affine=calibration,
    )
    crop = await _lens(ctx, dataset, slices)

    scene = await _scene_over(ctx, str(physical.pk), name="Calibrated")
    await _intensity_layer(ctx, scene["id"], crop["id"])
    layer = await _only_layer(ctx, scene["id"])

    assert [step["inverted"] for step in layer["pathToWorld"]] == [False, False], "lens -> intrinsic -> physical, both forward"
    matrix = layer["asAffine"]["matrix"]
    for index in np.ndindex(*view.shape):
        shifted = [float(i) for i in _apply(_matrix(crop["toParent"], 3), [float(i) for i in index])]
        expected = _apply(calibration, shifted)
        got = _apply(matrix, [float(i) for i in index])
        assert got == pytest.approx(expected, abs=1e-9), f"lens voxel {index}: {got} != {expected}"
        # And the voxel the calibration was applied to is the one holding the lens voxel's value.
        assert view[index] == arr[tuple(int(round(value)) for value in shifted)]


# --- 5. an annotation drawn on a crop --------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("slices", "point", "parent", "box"),
    [
        ([{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}], [0.0, 0.0], (1, 2), ([0.5, 1.5], [1.5, 2.5])),
        ([{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}], [3.0, 4.0], (4, 6), ([3.5, 5.5], [4.5, 6.5])),
        # A box is ± half a LENS voxel, and a lens voxel of a stepped lens is `step` dataset
        # voxels wide: the stored box is the stride the point stands for, not one dataset voxel.
        ([{"axis": "y", "start": 0, "stop": 6, "step": 2}, {"axis": "x", "start": 1, "stop": 8, "step": 3}], [1.0, 1.0], (2, 4), ([1.0, 2.5], [3.0, 5.5])),
    ],
    ids=["origin", "far-corner", "stepped"],
)
async def test_a_point_drawn_on_a_crop_is_stored_at_the_dataset_voxel(authenticated_context: HttpContext, slices: list[dict], point: list[float], parent: tuple[int, int], box: tuple[list[float], list[float]]):
    """A collection derived IDENTITY from the crop keeps its boxes in the dataset's grid, through the lens edge: the point's box is `start + step * (v ± 0.5)`."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    arr, view = _oracle([6, 8], ["y", "x"], slices)
    crop = await _lens(ctx, dataset, slices)

    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "LENS", "lens": crop["id"], "transform": {"kind": "IDENTITY"}})
    drawn = await _draw(ctx, collection["id"], "POINT", [point])

    stored = await models.AnnotationCollection.objects.aget(pk=collection["id"])
    assert stored.bbox_system_id == intrinsic.pk, "the walk crossed the lens edge and stopped at the dataset's grid"
    assert drawn["intrinsicBbox"] == {"min": box[0], "max": box[1]}
    assert view[int(point[0]), int(point[1])] == arr[parent]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rectangle_drawn_on_a_crop_spans_the_dataset_voxels_it_covers(authenticated_context: HttpContext):
    """Its box is the crop's corners shifted by the start, ± 0.5 -- the whole crop, drawn edge to edge, is the crop's own extent in the grid."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    crop = await _lens(ctx, dataset, [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "LENS", "lens": crop["id"], "transform": {"kind": "IDENTITY"}})

    drawn = await _draw(ctx, collection["id"], "RECTANGLE", [[0.0, 0.0], [0.0, 4.0], [3.0, 4.0], [3.0, 0.0]])

    assert drawn["intrinsicBbox"] == {"min": [0.5, 1.5], "max": [4.5, 6.5]}


# --- 6. the overlap boundary ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_overlaps_exactly_the_annotations_on_the_rows_it_selects(authenticated_context: HttpContext):
    """Points on the rows just outside a crop do not overlap it; the first and last rows inside do. Touching at a voxel face is not overlapping."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    whole = await _lens(ctx, dataset, [])
    crop = await _lens(ctx, dataset, [{"axis": "y", "start": 2, "stop": 5}])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})

    found = {}
    for row in (1, 2, 4, 5):
        drawn = await _draw(ctx, collection["id"], "POINT", [[float(row), 3.0]])
        found[row] = await _overlapping(ctx, drawn["id"])

    for row in (1, 2, 4, 5):
        assert whole["id"] in found[row], f"the whole lens covers row {row}"
    assert crop["id"] in found[2] and crop["id"] in found[4], "the first and last rows of the crop"
    assert crop["id"] not in found[1], "row 1 ends where the crop begins; a shared face is not an overlap"
    assert crop["id"] not in found[5], "row 5 begins where the crop ends"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stepped_crop_overlaps_exactly_the_rows_it_keeps_at_its_ends(authenticated_context: HttpContext):
    """`y 1:6:2` keeps rows 1, 3 and 5: a point on row 0 is outside it and one on row 5 inside.

    The lens' box is the footprint of the voxels it keeps, not `n·step` voxels from its start:
    pushing a plain `[-0.5, n-0.5]` box through `SCALE(2)` would stretch it to `[0, 6]` and call
    row 0 an overlap. A point on a skipped interior row still overlaps -- the extent is a box,
    and that stays a known limit.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    stepped = await _lens(ctx, dataset, [{"axis": "y", "start": 1, "stop": 6, "step": 2}])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})

    found = {}
    for row in (0, 1, 5):
        drawn = await _draw(ctx, collection["id"], "POINT", [[float(row), 3.0]])
        found[row] = await _overlapping(ctx, drawn["id"])

    assert stepped["id"] in found[1] and stepped["id"] in found[5], "the first and the last row the step lands on"
    assert stepped["id"] not in found[0], "row 0 ends where the kept row 1 begins, and the stretched box would have covered it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stepped_lens_extent_is_the_footprint_of_the_voxels_it_keeps(authenticated_context: HttpContext):
    """In the dataset's own grid, a stepped lens' extent is half a voxel around the first and the last voxel it keeps."""
    ctx = authenticated_context
    slices = [{"axis": "y", "start": 0, "stop": 6, "step": 2}, {"axis": "x", "start": 1, "stop": 8, "step": 3}]
    dataset = await seed.create_array_dataset(ctx, "Stepped", axes=seed.SIMPLE_AXES, shapes=[SHAPE])
    lens = await _lens(ctx, dataset, slices)

    arr, view = _oracle(SHAPE, ["c", "y", "x"], slices)
    kept = np.argwhere(np.isin(arr, view))
    expected = {axis: [float(kept[:, i].min()) - 0.5, float(kept[:, i].max()) + 0.5] for i, axis in enumerate(["c", "y", "x"])}

    def extents() -> dict:
        graph = space_graph.SpaceGraph(dataset.intrinsic_coordinate_system, organization=ctx.request.organization)
        return graph.lens_extents()

    extent = (await sync_to_async(extents)())[int(lens["id"])]
    assert extent == expected, "the box of the kept voxels: [-0.5, 4.5] on y (rows 0, 2, 4) and [0.5, 7.5] on x (columns 1, 4, 7)"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stepped_lens_keeps_only_the_anchors_on_the_indices_it_selects(authenticated_context: HttpContext):
    """`y 0:6:2` keeps rows 0, 2 and 4: an anchor pinned to row 1 is inside the range and outside the lens."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pinned", axes=seed.YX_AXES, shapes=[[6, 8]])
    lens = await seed.create_lens(ctx, dataset, slices=[{"axis": "y", "start": 0, "stop": 6, "step": 2}])
    for row in range(6):
        await models.CoordinateAnchor.objects.acreate(dataset=dataset, organization=ctx.request.organization, coordinates={"y": row})
    await models.CoordinateAnchor.objects.acreate(dataset=dataset, organization=ctx.request.organization, coordinates={})

    result = await schema.execute("query Anchors($id: ID!) { lens(id: $id) { activeAnchors { coordinates } } }", context_value=_fresh_request(ctx), variable_values={"id": str(lens.pk)})
    assert not result.errors, result.errors

    pinned = sorted(anchor["coordinates"].get("y", -1) for anchor in result.data["lens"]["activeAnchors"])
    assert pinned == [-1, *range(*slice(0, 6, 2).indices(6))], "the global anchor and the rows the step lands on; the odd rows are in range and not in the lens"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_windowed_table_lens_overlaps_the_shapes_in_its_window(authenticated_context: HttpContext):
    """A table the server cannot bound still has an extent where a window bounds it: x 10..20 meets a shape at x 10..20 and not one at 30..40. The whole lens, bounded nowhere, has not been shown to overlap either."""
    ctx = authenticated_context
    table, table_system = await _seed_table_dataset(ctx, "spots")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, system=table_system)
    whole = await seed.create_windowed_lens(ctx, table)
    window = await seed.create_windowed_lens(ctx, table, [{"axis": "x", "min": 10.0, "max": 20.0}])
    # Drawn on the table's own (y, x) space, which the scene's world then also reaches.
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "TABLE_DATASET", "tableDataset": str(table.pk), "transform": {"kind": "IDENTITY"}})

    inside = await _draw(ctx, collection["id"], "RECTANGLE", [[0.0, 10.0], [0.0, 20.0], [5.0, 20.0], [5.0, 10.0]])
    outside = await _draw(ctx, collection["id"], "RECTANGLE", [[0.0, 30.0], [0.0, 40.0], [5.0, 40.0], [5.0, 30.0]])

    assert str(window.pk) in await _overlapping(ctx, inside["id"])
    assert str(window.pk) not in await _overlapping(ctx, outside["id"])
    assert str(whole.pk) not in await _overlapping(ctx, inside["id"]), "bounded on no axis, the whole table lens has not been shown to overlap anything"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_annotation_lens_overlaps_the_shapes_in_its_window_on_its_own_collection(authenticated_context: HttpContext):
    """A window over a collection lives in the collection's own drawing space, which nothing is registered into; it is still found there."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Drawn on", axes=seed.YX_AXES, shapes=[[64, 64]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    row = await models.AnnotationCollection.objects.aget(pk=collection["id"])
    window = await seed.create_windowed_lens(ctx, row, [{"axis": "y", "min": 0.0, "max": 5.0}])

    inside = await _draw(ctx, collection["id"], "POINT", [[2.0, 3.0]])
    outside = await _draw(ctx, collection["id"], "POINT", [[10.0, 3.0]])

    assert str(window.pk) in await _overlapping(ctx, inside["id"])
    assert str(window.pk) not in await _overlapping(ctx, outside["id"])


# --- a crop of a crop -------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent, child",
    [
        ([{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 8}], [{"axis": "y", "start": 1, "stop": 3}, {"axis": "x", "start": -4}]),
        ([{"axis": "y", "start": 0, "stop": 6, "step": 2}], [{"axis": "y", "start": 1, "stop": 3}, {"axis": "c", "start": 1, "stop": 2}]),
        ([{"axis": "y", "start": 0, "stop": 6, "step": 2}, {"axis": "x", "start": 1, "stop": 8, "step": 3}], [{"axis": "x", "start": 0, "stop": 3, "step": 2}]),
        ([], [{"axis": "x", "start": 3}]),
    ],
)
async def test_a_crop_of_a_crop_is_the_datasets_lens_for_those_voxels(authenticated_context: HttpContext, parent: list[dict], child: list[dict]):
    """Slices stated over a lens compose through it: the result is the row the same voxels asked of the dataset give, and its edge maps every voxel."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Nested", axes=seed.SIMPLE_AXES, shapes=[SHAPE])
    outer = await _lens(ctx, dataset, parent)
    inner = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": outer["id"], "slices": child}})
    assert not inner.errors, inner.errors
    cut = inner.data["createLens"]

    arr = np.arange(math.prod(SHAPE)).reshape(SHAPE)
    by_axis = lambda slices: {entry["axis"]: entry for entry in slices}  # noqa: E731
    index = lambda slices: tuple(slice(by_axis(slices)[a].get("start"), by_axis(slices)[a].get("stop"), by_axis(slices)[a].get("step")) if a in by_axis(slices) else slice(None) for a in ["c", "y", "x"])  # noqa: E731
    view = arr[index(parent)][index(child)]

    assert cut["shape"] == list(view.shape)
    _assert_maps_every_voxel(_matrix(cut["toParent"], len(SHAPE)) if cut["toParent"] else [[1.0 if i == j else 0.0 for j in range(len(SHAPE) + 1)] for i in range(len(SHAPE))], arr, view)
    composed = [{"axis": a, "start": s.start, "stop": s.stop, "step": s.step} for a, s in zip(["c", "y", "x"], index(parent)) if s != slice(None)]
    direct = await _lens(ctx, dataset, [entry for entry in _compose_by_numpy(parent, child)])
    assert direct["id"] == cut["id"], "the same voxels asked of the dataset are the same row"
    del composed


def _compose_by_numpy(parent: list[dict], child: list[dict]) -> list[dict]:
    """The dataset slices numpy would need for `arr[parent][child]`, by composing index ranges."""
    out = []
    parent_by = {entry["axis"]: entry for entry in parent}
    child_by = {entry["axis"]: entry for entry in child}
    for axis, size in zip(["c", "y", "x"], SHAPE):
        p, c = parent_by.get(axis), child_by.get(axis)
        if p is None and c is None:
            continue
        kept = list(range(size))[slice(p.get("start"), p.get("stop"), p.get("step")) if p else slice(None)]
        if c is not None:
            kept = kept[slice(c.get("start"), c.get("stop"), c.get("step"))]
        step = (kept[1] - kept[0]) if len(kept) > 1 else 1
        out.append({"axis": axis, "start": kept[0], "stop": kept[-1] + 1, "step": step})
    return out


# --- a box in another space, and a crop to an annotation ----------------------------------------


async def _lens_from(ctx: HttpContext, dataset: models.ArrayDataset, **selection: object) -> dict | list:
    result = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"dataset": str(dataset.pk), **selection}})
    return result.errors if result.errors else result.data["createLens"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_box_in_micrometres_cuts_exactly_the_voxels_whose_cells_it_meets(authenticated_context: HttpContext):
    """Voxel j of a 0.5 um grid at offset 100 covers [100 + 0.5 j - 0.25, 100 + 0.5 j + 0.25): the oracle is that cell test, voxel by voxel, and the lens is the dataset's row for it."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Calibrated", shapes=[SHAPE])
    scale, offset = 0.5, 100.0
    physical = await seed.create_physical_space(
        ctx,
        dataset,
        axes=[seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."), seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", enums.AxisType.SPACE, "micrometer")],
        affine=[[1.0, 0.0, 0.0, 0.0], [0.0, scale, 0.0, offset], [0.0, 0.0, scale, offset]],
    )
    box = {"y": (100.5, 102.0), "x": (101.0, 103.4)}
    cut = await _lens_from(ctx, dataset, box={"coordinateSystem": str(physical.pk), "windows": [{"axis": axis, "min": lo, "max": hi} for axis, (lo, hi) in box.items()]})
    assert isinstance(cut, dict), cut

    expected = []
    for index, axis in enumerate(["y", "x"]):
        lo, hi = box[axis]
        kept = [j for j in range(SHAPE[index + 1]) if (offset + scale * j - scale / 2) < hi and lo < (offset + scale * j + scale / 2)]
        expected.append({"axis": axis, "start": kept[0], "stop": kept[-1] + 1})
    assert expected == [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 8}]
    arr, view = _oracle(SHAPE, ["c", "y", "x"], expected)
    assert cut["shape"] == list(view.shape)
    _assert_maps_every_voxel(_matrix(cut["toParent"], 3), arr, view)
    assert (await _lens(ctx, dataset, expected))["id"] == cut["id"], "the same voxels asked for as slices are the same row"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_box_in_a_siblings_grid_is_reached_through_the_world_they_share(authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    drawn_on = await seed.create_array_dataset(ctx, "DrawnOn", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    beside = await seed.create_array_dataset(ctx, "Beside", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    apart = await seed.create_array_dataset(ctx, "Apart", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    for dataset in (drawn_on, beside):
        await seed.register_into_scene(ctx, scene, dataset)
    beside_grid = await sync_to_async(lambda: beside.intrinsic_coordinate_system)()
    apart_grid = await sync_to_async(lambda: apart.intrinsic_coordinate_system)()

    cut = await _lens_from(ctx, drawn_on, box={"coordinateSystem": str(beside_grid.pk), "windows": [{"axis": "z", "min": 2, "max": 2}, {"axis": "y", "min": 10, "max": 20}, {"axis": "x", "min": 10, "max": 20}]})
    assert isinstance(cut, dict), cut
    assert cut["shape"] == [1, 11, 11], "both are registered as the identity into one world: beside's voxels are drawn_on's, 10..20 inclusive"

    refused = await _lens_from(ctx, drawn_on, box={"coordinateSystem": str(apart_grid.pk), "windows": [{"axis": "y", "min": 10, "max": 20}]})
    assert isinstance(refused, list) and "pixel grid" in refused[0].message, "a grid nothing connects to the dataset's"
    half_open = await _lens_from(ctx, drawn_on, box={"coordinateSystem": str(beside_grid.pk), "windows": [{"axis": "y", "min": 10}]})
    assert isinstance(half_open, dict) and half_open["shape"] == [8, 54, 64], "an open side runs to the array's edge, and an axis the box leaves out is whole"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_cut_to_an_annotation_bounds_exactly_the_shape(authenticated_context: HttpContext):
    """Crop to the ROI: a rectangle on rows 1..4 and columns 2..6 is the lens y[1:5], x[2:7], which overlaps that shape and not one outside it."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Flat", axes=seed.YX_AXES, shapes=[[6, 8]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", [[1.0, 2.0], [1.0, 6.0], [4.0, 6.0], [4.0, 2.0]])
    elsewhere = await _draw(ctx, collection["id"], "POINT", [[5.0, 3.0]])

    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict), cut
    arr, view = _oracle([6, 8], ["y", "x"], [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}])
    assert cut["shape"] == list(view.shape)
    _assert_maps_every_voxel(_matrix(cut["toParent"], 2), arr, view)
    assert cut["id"] in await _overlapping(ctx, rectangle["id"])
    assert cut["id"] not in await _overlapping(ctx, elsewhere["id"]), "row 5 begins where the crop ends"

    both = await _lens_from(ctx, dataset, annotation=rectangle["id"], slices=[{"axis": "y", "start": 0, "stop": 1}])
    assert isinstance(both, list) and "one selection" in both[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_with_fewer_components_than_its_space_is_cut_over_the_axes_it_spans(authenticated_context: HttpContext):
    """A rectangle on a (c, y, x) image has two components: they are y and x, never c and y. Unpinned it keeps every channel; pinned to one, that plane."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[SHAPE])
    collection = await _collection_on(ctx, axes=["c", "y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    corners = [[1.0, 2.0], [1.0, 6.0], [4.0, 6.0], [4.0, 2.0]]
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", corners)

    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict), cut
    planes = [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}]
    assert cut["shape"] == [SHAPE[0], 4, 5]
    assert (await _lens(ctx, dataset, planes))["id"] == cut["id"], "the channel axis is left whole"

    result = await schema.execute(CREATE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"collection": collection["id"], "kind": "RECTANGLE", "vectors": corners, "coordinates": [{"name": "c", "value": 1}]}})
    assert not result.errors, result.errors
    on_one_channel = await _lens_from(ctx, dataset, annotation=result.data["createAnnotation"]["id"])
    assert isinstance(on_one_channel, dict), on_one_channel
    assert on_one_channel["shape"] == [1, 4, 5]
    assert (await _lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 2}, *planes]))["id"] == on_one_channel["id"]


DRAW_ON_SCENE = "mutation Create($input: CreateAnnotationInput!) { createAnnotation(input: $input) { id collection { coordinateSystem { axes { name } } } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_drawn_on_a_scene_whose_world_has_channels_is_drawn_in_space(authenticated_context: HttpContext):
    """A scene over a (c, y, x) space mints a (y, x) drawing space: a vertex is a place, so a rectangle's first component is y and never a channel, and the lens cut to it keeps every channel."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Slide", shapes=[SHAPE])
    scene = await _scene_over(ctx, str(dataset.coordinate_system_id))
    assert [axis["name"] for axis in scene["worldCoordinateSystem"]["axes"]] == ["c", "y", "x"]

    result = await schema.execute(DRAW_ON_SCENE, context_value=_fresh_request(ctx), variable_values={"input": {"scene": scene["id"], "kind": "RECTANGLE", "vectors": [[1.0, 2.0], [4.0, 6.0]]}})
    assert not result.errors, result.errors
    drawn = result.data["createAnnotation"]
    assert [axis["name"] for axis in drawn["collection"]["coordinateSystem"]["axes"]] == ["y", "x"]

    cut = await _lens_from(ctx, dataset, annotation=drawn["id"])
    assert isinstance(cut, dict), cut
    assert cut["shape"] == [SHAPE[0], 4, 5]
    assert (await _lens(ctx, dataset, [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}]))["id"] == cut["id"]


# --- 7. levels ----------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_cropped_layer_walks_its_pyramid_from_the_whole_array(authenticated_context: HttpContext):
    """Pinned, not endorsed: `pathToWorld` starts at the crop, `levelPaths` start at each level's own grid and never cross the lens edge.

    A renderer reading level L of a cropped layer therefore applies the crop itself, where the
    crop's start lands at `(start - (f - 1) / 2) / f` in that level's indices. The server
    answers nothing finer than this, and this test is what says so.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", shapes=[SHAPE, [3, 3, 4]])
    intrinsic = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    crop = await _lens(ctx, dataset, [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}])

    scene = await _scene_over(ctx, str(intrinsic.pk))
    channel = await _intensity_layer(ctx, scene["id"], crop["id"])
    layer = await _only_layer(ctx, scene["id"], LEVEL_PATHS)

    assert [step["transformation"]["input"]["id"] for step in layer["pathToWorld"]] == [channel["coordinateSystem"]["id"]], "the channel lens' own space, cut from the crop, is where the path starts"
    by_level = {entry["dataArray"]["level"]: entry["path"] for entry in layer["levelPaths"]}
    assert by_level[0] == [], "level 0 is the world"
    assert [(step["inverted"], step["transformation"]["kind"]) for step in by_level[1]] == [(False, "SEQUENCE")], "level 1 -> intrinsic, and no lens edge"
    assert all(step["transformation"]["input"]["id"] not in (crop["coordinateSystem"]["id"], channel["coordinateSystem"]["id"]) for path in by_level.values() for step in path)


# --- 8. a repaired legacy edge ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_legacy_negative_start_edge_is_true_once_repaired(authenticated_context: HttpContext):
    """A lens written with `start: -5` recorded a translation of -5. After `merge_duplicate_lenses` it maps like the crop it always meant."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Small", shapes=[SHAPE])
    legacy = await sync_to_async(_legacy_sliced_lens)(ctx, dataset, [{"axis": "y", "start": -5, "stop": None, "step": None}])
    arr, view = _oracle(SHAPE, ["c", "y", "x"], [{"axis": "y", "start": 1}])

    def edge_matrix() -> list[list[float]]:
        edge = models.Transformation.objects.get(input_id=legacy.coordinate_system_id, output=dataset.coordinate_system)
        return _matrix({"translation": edge.params["translation"]}, 3)

    with pytest.raises(AssertionError):
        _assert_maps_every_voxel(await sync_to_async(edge_matrix)(), arr, view)

    await sync_to_async(call_command)("merge_duplicate_lenses", stdout=StringIO())

    _assert_maps_every_voxel(await sync_to_async(edge_matrix)(), arr, view)
    repaired = await models.Lens.objects.aget(pk=legacy.pk)
    assert repaired.slices == [{"axis": "y", "start": 1, "stop": 6, "step": 1}]
