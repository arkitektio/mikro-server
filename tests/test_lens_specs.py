"""A lens states the spec of what it selects, in its container's vocabulary.

Computed from the container and the selection and materialized when the lens is made: both
are immutable, so the stored list cannot drift, and the lens filters read it in SQL.
One rule for every kind: an axis the selection holds to a single position does not count. An
array lens knows its extents, so that is its shape; a windowed lens knows only its windows, so
it is a window whose min equals its max. A window that merely bounds changes nothing.

The same list is the lens' ``@mikro/spec`` descriptor, which is what a port requires.
"""

from types import SimpleNamespace

import pytest
from kante.context import HttpContext

from chart.logic import chart as chart_logic
from core import enums, models
from core.enums import SparseDatasetSpec as Sparse
from core.enums import TableDatasetSpec as Table
from core.logic.coords import METRIC_AXIS_TYPES, AxisSpec, ColumnSpec, specs_for_axes, specs_for_columns, specs_for_sparse
from mikro_server.schema import schema
from tests import seed
from tests.test_sparse_datasets import SHAPE, _colour, _expression, _placed_mask, _slice
from tests.test_table_specs import TRACKS
from tests.test_table_specs import _create as _create_table

LENS = """
query Lens($id: ID!) {
  lens(id: $id) {
    descriptors
    ... on ArrayLens { arraySpec: spec }
    ... on TableLens { tableSpec: spec }
    ... on SparseLens { sparseSpec: spec }
  }
}
"""


def _window(axis: str, low: float | None, high: float | None) -> SimpleNamespace:
    return SimpleNamespace(axis=axis, min=low, max=high)


_TRACK_COLUMNS = [
    ColumnSpec(name="t", role="COORDINATE", axis_type="TIME", dtype="DOUBLE"),
    ColumnSpec(name="z", role="COORDINATE", axis_type="SPACE", dtype="DOUBLE"),
    ColumnSpec(name="y", role="COORDINATE", axis_type="SPACE", dtype="DOUBLE"),
    ColumnSpec(name="x", role="COORDINATE", axis_type="SPACE", dtype="DOUBLE"),
    ColumnSpec(name="track", role="TRACK_ID", axis_type=None, dtype="BIGINT"),
    ColumnSpec(name="photons", role="ATTRIBUTE", axis_type=None, dtype="DOUBLE"),
]


@pytest.mark.parametrize(
    ("pinned", "expected"),
    [
        ([], [Table.POINTS_3D, Table.TIMED, Table.TRACKED]),
        (["t"], [Table.POINTS_3D, Table.TRACKED]),
        (["z"], [Table.POINTS_2D, Table.TIMED, Table.TRACKED]),
        # What is left is one measured coordinate and a numeric value: a profile along x over time is not, a profile along x is.
        (["z", "y"], [Table.POINTS_1D, Table.TIMED, Table.TRACKED]),
        (["t", "z", "y"], [Table.POINTS_1D, Table.TRACKED, Table.SERIES]),
        (["z", "y", "x"], [Table.RECORDS, Table.TIMED, Table.TRACKED, Table.SERIES]),
        # Only a coordinate is held by a window; a name that is not one changes nothing.
        (["photons"], [Table.POINTS_3D, Table.TIMED, Table.TRACKED]),
    ],
)
def test_a_pinned_coordinate_column_does_not_count(pinned: list[str], expected: list[Table]) -> None:
    assert specs_for_columns(_TRACK_COLUMNS, pinned) == expected


@pytest.mark.parametrize(
    ("windows", "expected"),
    [
        ([], [Sparse.MATRIX]),
        ([_window("feature", 7.0, 7.0)], [Sparse.VECTOR, Sparse.SLICE]),
        # Bounded, not pinned: nobody has counted what is left along the axis.
        ([_window("feature", 0.0, 7.0)], [Sparse.MATRIX]),
        # The axis a source supplies ids for: one object's profile is a vector and not a slice.
        ([_window("object", 3.0, 3.0)], [Sparse.VECTOR]),
        ([_window("feature", 7.0, 7.0), _window("object", 3.0, 3.0)], [Sparse.SCALAR]),
        # Not a position: past the end, or between two.
        ([_window("feature", 40.0, 40.0)], [Sparse.VECTOR]),
        ([_window("feature", 7.5, 7.5)], [Sparse.VECTOR]),
    ],
)
def test_a_sparse_selection_is_ranked_by_the_axes_it_leaves_free(windows: list, expected: list[Sparse]) -> None:
    assert specs_for_sparse(["feature", "object"], [40, 12], identified=["feature"], indexed=["feature", "object"], windows=windows) == expected


def test_a_slice_needs_a_layout_that_reads_it_in_one_range() -> None:
    pin = [_window("feature", 7.0, 7.0)]
    assert Sparse.SLICE not in specs_for_sparse(["feature", "object"], [40, 12], identified=["feature"], indexed=["object"], windows=pin)
    assert specs_for_sparse(["feature", "object"], [1, 12], identified=["feature"], indexed=["feature"]) == [Sparse.VECTOR], "an axis of one position does not count"


_T, _C, _Z, _Y, _X, _I = (AxisSpec(name, kind) for name, kind in (("t", "TIME"), ("c", "CHANNEL"), ("z", "SPACE"), ("y", "SPACE"), ("x", "SPACE"), ("i", "INDEX")))


@pytest.mark.parametrize(
    ("axes", "shape"),
    [
        ([_C, _Y, _X], [3, 64, 64]),
        ([_C, _Y, _X], [3, 1, 64]),
        ([_C, _Y, _X], [1, 1, 64]),
        ([_C, _Y, _X], [1, 1, 1]),
        ([_T, _C, _Y, _X], [5, 3, 1, 1]),
        ([_T, _C, _Y, _X], [5, 1, 1, 9]),
        ([_T, _C, _I], [5, 3, 4]),
        ([_T, _I], [5, 4]),
        ([_C, _I], [3, 4]),
        ([_Z, _Y, _X], [1, 1, 7]),
    ],
)
def test_a_selection_is_a_trace_exactly_when_a_chart_draws_it_as_one(axes: list[AxisSpec], shape: list[int]) -> None:
    """The spec and the chart's own gate are two statements of one rule; this holds them together."""
    lens = SimpleNamespace(pk=1, axis_specs=axes, axis_names=[axis.name for axis in axes], shape_list=shape)

    def drawable(along: str) -> bool:
        try:
            chart_logic.assert_trace(lens, along)
        except ValueError:
            return False
        return True

    measured = [axis.name for axis in axes if axis.type in METRIC_AXIS_TYPES]
    assert (enums.ArrayDatasetSpec.TRACE in specs_for_axes(axes, shape)) is any(drawable(along) for along in measured)


async def _lens(ctx: HttpContext, pk: object) -> dict:
    result = await schema.execute(LENS, context_value=ctx, variable_values={"id": str(pk)})
    assert not result.errors, result.errors
    return result.data["lens"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_array_lens_answers_the_spec_of_its_crop(authenticated_context: HttpContext):
    axes = [seed.axis("c", enums.AxisType.CHANNEL), seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
    dataset = await seed.create_array_dataset(authenticated_context, "stack", axes=axes, shapes=[[3, 8, 64, 64]])

    whole = await _lens(authenticated_context, (await seed.create_lens(authenticated_context, dataset)).pk)
    plane = await _lens(authenticated_context, (await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "z", "start": 4, "stop": 5}])).pk)
    row = await _lens(
        authenticated_context,
        (await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}, {"axis": "z", "start": 4, "stop": 5}, {"axis": "y", "start": 9, "stop": 10}])).pk,
    )

    await dataset.arefresh_from_db(fields=["stored_spec"])
    assert whole["arraySpec"] == dataset.stored_spec == ["VOLUME", "MULTICHANNEL"], "the whole lens is its dataset"
    assert plane["arraySpec"] == ["IMAGE", "MULTICHANNEL"]
    assert row["arraySpec"] == ["PROFILE", "TRACE"]
    for lens in (whole, plane, row):
        assert lens["descriptors"]["@mikro/spec"] == lens["arraySpec"], "the descriptor a port requires is the field"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_lens_answers_the_spec_of_what_its_windows_leave_free(authenticated_context: HttpContext):
    created = await _create_table(authenticated_context, "tracks", TRACKS)
    table = await models.TableDataset.objects.aget(pk=created["id"])

    whole = await _lens(authenticated_context, (await seed.create_windowed_lens(authenticated_context, table)).pk)
    bounded = await _lens(authenticated_context, (await seed.create_windowed_lens(authenticated_context, table, [{"axis": "t", "min": 0.0, "max": 10.0}])).pk)
    frame = await _lens(authenticated_context, (await seed.create_windowed_lens(authenticated_context, table, [{"axis": "t", "min": 4.0, "max": 4.0}])).pk)

    assert whole["tableSpec"] == created["spec"] == ["POINTS_2D", "TIMED", "TRACKED"], "the whole lens is its table"
    assert bounded["tableSpec"] == ["POINTS_2D", "TIMED", "TRACKED"], "a window that bounds says nothing about how the rows spread"
    assert frame["tableSpec"] == ["POINTS_2D", "TRACKED"], "one instant is not a time course"

    for lens in (whole, bounded, frame):
        assert lens["descriptors"]["@mikro/spec"] == lens["tableSpec"]
        # The schema facts stay the table's: a window keeps every column.
        assert lens["descriptors"]["@mikro/n_time_axes"] == 1 and lens["descriptors"]["@mikro/has_track_ids"] is True
    assert (whole["descriptors"]["@mikro/is_whole"], frame["descriptors"]["@mikro/is_whole"]) == (True, False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_lens_is_a_slice_exactly_when_a_picker_takes_it(authenticated_context: HttpContext):
    """SLICE and the colour picker's gate are two statements of one rule; this holds them together."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    both = await _expression(authenticated_context, mask)

    result = await schema.execute("query S($id: ID!) { sparseDataset(id: $id) { spec descriptors } }", context_value=authenticated_context, variable_values={"id": both})
    assert not result.errors, result.errors
    assert result.data["sparseDataset"]["spec"] == result.data["sparseDataset"]["descriptors"]["@mikro/spec"] == ["MATRIX"]

    for positions, spec in (({"feature": 7}, ["VECTOR", "SLICE"]), ({"object": 3}, ["VECTOR"]), ({"feature": SHAPE[0]}, ["VECTOR"])):
        slice_id = await _slice(authenticated_context, both, **positions)
        answered = await _lens(authenticated_context, slice_id)
        assert answered["sparseSpec"] == answered["descriptors"]["@mikro/spec"] == spec
        coloured = await _colour(authenticated_context, lens, scene, {"kind": "SPARSE", "lens": slice_id, "colormap": "MAGMA"})
        assert (not coloured.errors) is ("SLICE" in spec), coloured.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_slice_the_store_would_scan_for_is_not_one(authenticated_context: HttpContext):
    mask, lens, scene = await _placed_mask(authenticated_context)
    by_object = await _expression(authenticated_context, mask, axes=(1,))

    slice_id = await _slice(authenticated_context, by_object, feature=7)
    assert (await _lens(authenticated_context, slice_id))["sparseSpec"] == ["VECTOR"]
    coloured = await _colour(authenticated_context, lens, scene, {"kind": "SPARSE", "lens": slice_id, "colormap": "MAGMA"})
    assert coloured.errors


FILTERED = "query Lenses($filters: LensFilter!) { lenses(filters: $filters) { id } }"


async def _matching(ctx: HttpContext, **filters: list[str]) -> set[str]:
    result = await schema.execute(FILTERED, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {lens["id"] for lens in result.data["lenses"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lenses_are_found_by_the_spec_of_what_they_select(authenticated_context: HttpContext):
    """One filter per vocabulary, each over its own kind: the member names overlap, the lenses do not."""
    ctx = authenticated_context
    axes = [seed.axis("c", enums.AxisType.CHANNEL), seed.axis("z", enums.AxisType.SPACE), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
    dataset = await seed.create_array_dataset(ctx, "stack", axes=axes, shapes=[[3, 8, 64, 64]])
    whole = str((await seed.create_lens(ctx, dataset)).pk)
    plane = str((await seed.create_lens(ctx, dataset, slices=[{"axis": "z", "start": 4, "stop": 5}])).pk)
    channel = str((await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}, {"axis": "z", "start": 4, "stop": 5}])).pk)
    voxel = str((await seed.create_lens(ctx, dataset, slices=[{"axis": name, "start": 0, "stop": 1} for name in "czyx"])).pk)

    table = await models.TableDataset.objects.aget(pk=(await _create_table(ctx, "tracks", TRACKS))["id"])
    tracks = str((await seed.create_windowed_lens(ctx, table)).pk)
    frame = str((await seed.create_windowed_lens(ctx, table, [{"axis": "t", "min": 4.0, "max": 4.0}])).pk)

    mask, _, _ = await _placed_mask(ctx)
    matrix = await _expression(ctx, mask)
    feature = await _slice(ctx, matrix, feature=7)
    cell = await _slice(ctx, matrix, feature=7, object=3)

    assert await _matching(ctx, arraySpec=["VOLUME"]) == {whole}
    assert await _matching(ctx, arraySpec=["IMAGE"]) >= {plane, channel} and whole not in await _matching(ctx, arraySpec=["IMAGE"])
    assert {plane, channel} & await _matching(ctx, arraySpec=["IMAGE", "MULTICHANNEL"]) == {plane}
    assert await _matching(ctx, tableSpec=["POINTS_2D", "TRACKED"]) == {tracks, frame}
    assert await _matching(ctx, tableSpec=["TIMED"]) == {tracks}
    assert await _matching(ctx, sparseSpec=["SLICE"]) == {feature}
    # SCALAR is a member of two vocabularies; each filter answers for its own kind only.
    assert voxel in await _matching(ctx, arraySpec=["SCALAR"]) and cell not in await _matching(ctx, arraySpec=["SCALAR"])
    assert await _matching(ctx, sparseSpec=["SCALAR"]) == {cell}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_setup_job_states_the_spec_of_a_lens_written_without_one(authenticated_context: HttpContext):
    """What gives a spec to the lenses older than the column, and to any row a rolled-back release wrote."""
    from io import StringIO

    from asgiref.sync import sync_to_async
    from django.core.management import call_command

    ctx = authenticated_context
    axes = [seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]
    dataset = await seed.create_array_dataset(ctx, "image", axes=axes, shapes=[[3, 64, 64]])
    channel = await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}])
    table = await models.TableDataset.objects.aget(pk=(await _create_table(ctx, "tracks", TRACKS))["id"])
    frame = await seed.create_windowed_lens(ctx, table, [{"axis": "t", "min": 4.0, "max": 4.0}])
    stated = {str(pk): spec async for pk, spec in models.Lens.objects.values_list("pk", "stored_spec")}
    assert stated[str(channel.pk)] == ["IMAGE"] and stated[str(frame.pk)] == ["POINTS_2D", "TRACKED"]

    await models.Lens.objects.all().aupdate(stored_spec=[])
    out = StringIO()
    await sync_to_async(call_command)("merge_duplicate_lenses", stdout=out)
    assert {str(pk): spec async for pk, spec in models.Lens.objects.values_list("pk", "stored_spec")} == stated
    assert f"stated the spec of {sum(1 for spec in stated.values() if spec)} lens(es)" in out.getvalue()

    again = StringIO()
    await sync_to_async(call_command)("merge_duplicate_lenses", stdout=again)
    assert "stated the spec of 0 lens(es)" in again.getvalue(), "nothing is owed the second time"
