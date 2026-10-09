"""The pure arithmetic of selections: a window as a box, a crop of a crop, a window of a window, a box in another space as slices.

No database: every case is checked against numpy or against the definition, because this is
the arithmetic the lens mutations and the overlap filter stand on, and "obviously equivalent"
is how a half-voxel goes missing.
"""

import math

import numpy as np
import pytest

from core.logic import coords
from core.logic.coords import AxedForm


class _Slice:
    def __init__(self, axis: str, start: int | None = None, stop: int | None = None, step: int | None = None) -> None:
        self.axis, self.start, self.stop, self.step = axis, start, stop, step


class _Window:
    def __init__(self, axis: str, min: float | None = None, max: float | None = None) -> None:  # noqa: A002 - the field names
        self.axis, self.min, self.max = axis, min, max


SHAPE = [3, 6, 8]
AXES = ["c", "y", "x"]


def _index(slices: list, axis_names: list[str]) -> tuple:
    by_axis = {s.axis: s for s in slices}
    return tuple(slice(by_axis[a].start, by_axis[a].stop, by_axis[a].step) if a in by_axis else slice(None) for a in axis_names)


# --- a crop of a crop -------------------------------------------------------------------


@pytest.mark.parametrize(
    "parent, child",
    [
        ([_Slice("y", 1, 5)], [_Slice("y", 1, 3)]),
        ([_Slice("y", 1, 5), _Slice("x", 2, 8)], [_Slice("y", -2), _Slice("x", 0, 4, 2)]),
        ([_Slice("y", 0, 6, 2)], [_Slice("y", 1, 3)]),
        ([_Slice("y", 0, 6, 2), _Slice("x", 1, 8, 3)], [_Slice("y", 0, 3, 2), _Slice("x", -2)]),
        ([_Slice("c", 1, 3)], [_Slice("y", 2, 400)]),
        ([], [_Slice("x", 3)]),
        ([_Slice("x", 1, 7, 2)], [_Slice("x", None, None, 2)]),
    ],
)
def test_a_crop_of_a_crop_is_the_crop_numpy_takes(parent: list, child: list) -> None:
    arr = np.arange(math.prod(SHAPE)).reshape(SHAPE)
    expected = arr[_index(parent, AXES)][_index(child, AXES)]
    composed = coords.compose_slices(SHAPE, AXES, parent, child)
    normalized = coords.normalize_slices(SHAPE, AXES, [_Slice(**entry) for entry in composed])
    got = arr[_index([_Slice(**entry) for entry in normalized], AXES)]
    assert got.shape == expected.shape and np.array_equal(got, expected), (composed, normalized)


def test_a_child_slice_that_keeps_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="keeps nothing"):
        coords.compose_slices(SHAPE, AXES, [_Slice("y", 1, 3)], [_Slice("y", 5, 9)])
    with pytest.raises(ValueError, match="positive"):
        coords.compose_slices(SHAPE, AXES, [], [_Slice("y", None, None, -1)])
    with pytest.raises(ValueError, match="does not have"):
        coords.compose_slices(SHAPE, AXES, [], [_Slice("t", 0, 1)])


# --- a window of a window ---------------------------------------------------------------


def test_a_window_of_a_window_is_the_tighter_bounds_and_an_open_side_stays_open() -> None:
    got = coords.intersect_windows([_Window("t", 2.0, 8.0), _Window("x", None, 10.0)], [_Window("t", 5.0, 20.0), _Window("y", 1.0, None)])
    assert {entry["axis"]: (entry["min"], entry["max"]) for entry in got} == {"t": (5.0, 8.0), "x": (None, 10.0), "y": (1.0, None)}
    with pytest.raises(ValueError, match="keeps nothing"):
        coords.normalize_windows(["t"], [_Window(**entry) for entry in coords.intersect_windows([_Window("t", 2.0, 8.0)], [_Window("t", 9.0, 20.0)])])


# --- a window as a box -------------------------------------------------------------------


def test_a_window_box_bounds_the_axes_it_names_and_nothing_else() -> None:
    mins, maxs = coords.window_box(["t", "y", "x"], [_Window("x", 10.0, 20.0), _Window("t", None, 5.0)])
    assert (mins, maxs) == ([-math.inf, -math.inf, 10.0], [5.0, math.inf, 20.0])
    forms = {"t": AxedForm((1.0, 0.0, 0.0), 0.0), "y": AxedForm((0.0, 1.0, 0.0), 0.0), "x": AxedForm((0.0, 0.0, 2.0), 100.0)}
    box = coords.axed_bbox(mins, maxs, forms)
    assert coords.bounded_axes(box) == {"x": [120.0, 140.0]}, "the open axes are dropped, not written as infinities"
    assert coords.shares_a_bounded_axis({"x": [0, 1]}, {"x": [0, 1], "y": [0, 1]}) and not coords.shares_a_bounded_axis({"t": [0, 1]}, {"x": [0, 1]})


# --- a box in another space, as slices ---------------------------------------------------


def _physical(scale: float, translation: float) -> dict[str, AxedForm]:
    """Micrometres -> voxels, for a grid registered at `scale` um per voxel and offset `translation` um, on (y, x)."""
    return {"y": AxedForm((1.0 / scale, 0.0), -translation / scale), "x": AxedForm((0.0, 1.0 / scale), -translation / scale)}


def test_a_box_in_micrometres_keeps_exactly_the_voxels_whose_cells_it_meets() -> None:
    """Voxel j of a 0.5 um grid at offset 100 covers [100 + 0.5 j - 0.25, 100 + 0.5 j + 0.25): the oracle is that cell test, voxel by voxel."""
    scale, offset = 0.5, 100.0
    lo, hi = [100.5, 101.0], [102.0, 103.4]
    got = coords.box_to_slices(_physical(scale, offset), ["y", "x"], lo, hi, ["y", "x"], [6, 8])
    by_axis = {entry["axis"]: entry for entry in got}
    for index, axis in enumerate(["y", "x"]):
        kept = [j for j in range(8) if (offset + scale * j - scale / 2) < hi[index] and lo[index] < (offset + scale * j + scale / 2)]
        kept = [j for j in kept if j < [6, 8][index]]
        assert list(range(by_axis[axis]["start"], by_axis[axis]["stop"])) == kept, axis


def test_a_box_on_a_voxel_face_does_not_take_the_voxel_beyond_it() -> None:
    identity = {"y": AxedForm((1.0, 0.0), 0.0), "x": AxedForm((0.0, 1.0), 0.0)}
    got = coords.box_to_slices(identity, ["y", "x"], [0.5, 1.5], [4.5, 6.5], ["y", "x"], [6, 8])
    assert got == [{"axis": "y", "start": 1, "stop": 5, "step": 1}, {"axis": "x", "start": 2, "stop": 7, "step": 1}], "faces at halves: [0.5, 4.5] is voxels 1..4"
    with pytest.raises(ValueError, match="outside the dataset"):
        coords.box_to_slices(identity, ["y", "x"], [10.0, 0.0], [12.0, 1.0], ["y", "x"], [6, 8])
    with pytest.raises(ValueError, match="open along 'x'"):
        coords.box_to_slices(identity, ["y", "x"], [0.0, -math.inf], [1.0, math.inf], ["y", "x"], [6, 8])


def test_an_axis_the_map_does_not_reach_is_whole_unless_pinned() -> None:
    plane = {"y": AxedForm((1.0, 0.0), 0.0), "x": AxedForm((0.0, 1.0), 0.0)}
    assert coords.box_to_slices(plane, ["y", "x"], [1.0, 2.0], [3.0, 5.0], ["c", "y", "x"], SHAPE) == [{"axis": "y", "start": 1, "stop": 4, "step": 1}, {"axis": "x", "start": 2, "stop": 6, "step": 1}]
    assert coords.box_to_slices(plane, ["y", "x"], [1.0, 2.0], [3.0, 5.0], ["c", "y", "x"], SHAPE, pins={"c": 1})[0] == {"axis": "c", "start": 1, "stop": 2, "step": 1}
    with pytest.raises(ValueError, match="outside"):
        coords.box_to_slices(plane, ["y", "x"], [1.0, 2.0], [3.0, 5.0], ["c", "y", "x"], SHAPE, pins={"c": 7})
