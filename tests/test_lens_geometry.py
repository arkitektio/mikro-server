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
    toParent { ...Edge ... on SequenceTransformation { transformations { ...Edge } } }
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
    if "transformations" in edge:
        steps = edge["transformations"]
        scale = next(step["scale"] for step in steps if "scale" in step)
        translation = next(step["translation"] for step in steps if "translation" in step)
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


async def _intensity_layer(ctx: HttpContext, scene_id: str, lens_id: str) -> None:
    result = await schema.execute(CREATE_INTENSITY, context_value=_fresh_request(ctx), variable_values={"input": {"scene": scene_id, "lens": lens_id, "intensityAxis": "c"}})
    assert not result.errors, result.errors


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
    await _intensity_layer(ctx, scene["id"], lens["id"])
    layer = await _only_layer(ctx, scene["id"])

    assert layer["placement"] == "PLACED"
    assert [(step["inverted"], step["transformation"]["id"]) for step in layer["pathToWorld"]] == [(False, lens["toParent"]["id"])]
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
    await _intensity_layer(ctx, scene["id"], whole["id"])
    layer = await _only_layer(ctx, scene["id"])

    assert [(step["inverted"], step["transformation"]["id"]) for step in layer["pathToWorld"]] == [(True, crop["toParent"]["id"])]
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
    await _intensity_layer(ctx, scene["id"], crop["id"])
    layer = await _only_layer(ctx, scene["id"], LEVEL_PATHS)

    assert [step["transformation"]["input"]["id"] for step in layer["pathToWorld"]] == [crop["coordinateSystem"]["id"]]
    by_level = {entry["dataArray"]["level"]: entry["path"] for entry in layer["levelPaths"]}
    assert by_level[0] == [], "level 0 is the world"
    assert [(step["inverted"], step["transformation"]["kind"]) for step in by_level[1]] == [(False, "SEQUENCE")], "level 1 -> intrinsic, and no lens edge"
    assert all(step["transformation"]["input"]["id"] != crop["coordinateSystem"]["id"] for path in by_level.values() for step in path)


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
        edge = models.Transformation.objects.get(input_id=legacy.coordinate_system_id, output=dataset.coordinate_system, parent__isnull=True)
        return _matrix({"translation": edge.params["translation"]}, 3)

    with pytest.raises(AssertionError):
        _assert_maps_every_voxel(await sync_to_async(edge_matrix)(), arr, view)

    await sync_to_async(call_command)("merge_duplicate_lenses", stdout=StringIO())

    _assert_maps_every_voxel(await sync_to_async(edge_matrix)(), arr, view)
    repaired = await models.Lens.objects.aget(pk=legacy.pk)
    assert repaired.slices == [{"axis": "y", "start": 1, "stop": 6, "step": 1}]
