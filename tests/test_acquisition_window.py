"""When a dataset was acquired: stated on its anchors, derived onto the dataset.

The anchors' acquisition windows are the truth; ``acquisitionStart`` / ``acquisitionEnd`` cache
their envelope on the dataset (``core.derived``), so a list filters by time without a join. The
cache stays true: a dataset whose anchors contradict each other gets no time at all, and a
``DerivationConflict`` saying why.
"""

import datetime
from unittest.mock import patch

import pytest
from arkitekt_datalayer.models import ZarrStore
from kante.context import HttpContext

from core import derived, models
from core.derived import Ambiguous, Conflict, Stated, Statement, agreement, envelope
from mikro_server.schema import schema


CREATE = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id acquisitionStart acquisitionEnd }
}
"""

ATTACH = """
mutation Attach($input: CreateCoordinateAnchorInput!) {
  createCoordinateAnchor(input: $input) { id acquisitionWindow { start end } }
}
"""

READ = """
query Read($id: ID!) {
  arrayDataset(id: $id) { acquisitionStart acquisitionEnd derivationConflicts { field reason anchors } }
}
"""

LIST = """
query List($filters: ArrayDatasetFilter) {
  arrayDatasets(filters: $filters) { id }
}
"""


def at(hour: int, minute: int = 0) -> str:
    return datetime.datetime(2026, 10, 22, hour, minute, tzinfo=datetime.UTC).isoformat()


def when(value: str | None) -> datetime.datetime | None:
    return None if value is None else datetime.datetime.fromisoformat(value)


def anchor(window: tuple[str, str | None] | None, **coordinates: int) -> dict:
    stated: dict = {"axisAnchors": [{"axis": axis, "value": value} for axis, value in coordinates.items()]}
    if window is not None:
        stated["acquisitionWindow"] = {"start": window[0], "end": window[1]}
    return stated


async def _create(ctx: HttpContext, name: str, anchors: list[dict]):
    store = await ZarrStore.objects.acreate(
        organization=ctx.request.organization, key=name, bucket="zarr", shape=[3, 64, 64], chunks=[1, 64, 64], version="3", dtype="uint8", populated=True
    )
    variables = {
        "input": {
            "data": str(store.id),
            "name": name,
            "scales": [],
            "axes": [{"name": "t", "type": "TIME"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
            "anchors": anchors,
        }
    }
    with patch("arkitekt_datalayer.models.ZarrStore.fill_info", return_value=None):
        return await schema.execute(CREATE, context_value=ctx, variable_values=variables)


async def _created(ctx: HttpContext, name: str, anchors: list[dict]) -> dict:
    result = await _create(ctx, name, anchors)
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]


async def _read(ctx: HttpContext, dataset_id: str) -> dict:
    result = await schema.execute(READ, context_value=ctx, variable_values={"id": dataset_id})
    assert not result.errors, result.errors
    return result.data["arrayDataset"]


async def _attach(ctx: HttpContext, dataset_id: str, stated: dict) -> None:
    result = await schema.execute(ATTACH, context_value=ctx, variable_values={"input": {"dataset": dataset_id, "anchor": stated}})
    assert not result.errors, result.errors


async def _listed(ctx: HttpContext, filters: dict) -> set[str]:
    result = await schema.execute(LIST, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {dataset["id"] for dataset in result.data["arrayDatasets"]}


# --- derived at ingest ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_on_the_whole_dataset_is_the_datasets(authenticated_context: HttpContext):
    created = await _created(authenticated_context, "whole", [anchor((at(10), at(10, 5)))])
    assert (when(created["acquisitionStart"]), when(created["acquisitionEnd"])) == (when(at(10)), when(at(10, 5))), "the mutation answers with the columns it derived"
    read = await _read(authenticated_context, created["id"])
    assert (when(read["acquisitionStart"]), when(read["acquisitionEnd"])) == (when(at(10)), when(at(10, 5)))
    assert read["derivationConflicts"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_frames_give_the_envelope_and_one_instant_is_a_span(authenticated_context: HttpContext):
    created = await _created(authenticated_context, "frames", [anchor((at(10), None), t=0), anchor((at(10, 1), at(10, 2)), t=1), anchor((at(10, 4), None), t=2)])
    read = await _read(authenticated_context, created["id"])
    assert (when(read["acquisitionStart"]), when(read["acquisitionEnd"])) == (when(at(10)), when(at(10, 4)))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_stated_time_is_no_time(authenticated_context: HttpContext):
    created = await _created(authenticated_context, "untimed", [anchor(None)])
    read = await _read(authenticated_context, created["id"])
    assert (read["acquisitionStart"], read["acquisitionEnd"]) == (None, None)


# --- following the anchors after ingest -------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_columns_follow_windows_attached_replaced_and_removed(authenticated_context: HttpContext):
    created = await _created(authenticated_context, "followed", [anchor((at(10), None), t=0)])

    await _attach(authenticated_context, created["id"], anchor((at(11), None), t=2))
    read = await _read(authenticated_context, created["id"])
    assert when(read["acquisitionEnd"]) == when(at(11)), "a window attached later widens the dataset's"

    await _attach(authenticated_context, created["id"], anchor((at(10, 30), None), t=2))
    read = await _read(authenticated_context, created["id"])
    assert when(read["acquisitionEnd"]) == when(at(10, 30)), "a window stated again replaces the first"

    await models.AcquisitionWindow.objects.filter(anchor__dataset_id=created["id"], anchor__coordinates={"t": 2}).adelete()
    # A queryset delete sends post_delete per row, so the columns follow it too.
    read = await _read(authenticated_context, created["id"])
    assert (when(read["acquisitionStart"]), when(read["acquisitionEnd"])) == (when(at(10)), when(at(10)))

    await models.CoordinateAnchor.objects.filter(dataset_id=created["id"]).adelete()
    read = await _read(authenticated_context, created["id"])
    assert (read["acquisitionStart"], read["acquisitionEnd"]) == (None, None), "with its anchors gone, the dataset states no time"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_with_conflicts_deletes_cleanly(authenticated_context: HttpContext):
    """Its anchors cascade with it: their deletes must not derive conflicts onto a dataset going away."""
    created = await _created(authenticated_context, "doomed", [anchor((at(10), at(10, 5))), anchor((at(11), None), t=1)])
    await models.ArrayDataset.objects.filter(pk=created["id"]).adelete()
    assert not await models.DerivationConflict.objects.filter(dataset_id=created["id"]).aexists()


# --- nonsensical metadata ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_frame_outside_the_whole_datasets_window_is_a_conflict(authenticated_context: HttpContext):
    created = await _created(authenticated_context, "contradicted", [anchor((at(10), at(10, 5))), anchor((at(11), None), t=1)])
    read = await _read(authenticated_context, created["id"])
    assert (read["acquisitionStart"], read["acquisitionEnd"]) == (None, None), "a cache that cannot be true is not written"
    assert {conflict["field"] for conflict in read["derivationConflicts"]} == {"acquisition_start", "acquisition_end"}
    ids = {anchor_id async for anchor_id in models.CoordinateAnchor.objects.filter(dataset_id=created["id"]).values_list("pk", flat=True)}
    assert {int(anchor_id) for conflict in read["derivationConflicts"] for anchor_id in conflict["anchors"]} == ids
    assert "t=1" in read["derivationConflicts"][0]["reason"]

    assert created["id"] in await _listed(authenticated_context, {"hasDerivationConflicts": True})
    assert created["id"] not in await _listed(authenticated_context, {"hasDerivationConflicts": False})

    await _attach(authenticated_context, created["id"], anchor((at(10, 3), None), t=1))
    read = await _read(authenticated_context, created["id"])
    assert read["derivationConflicts"] == [], "correcting the frame clears the conflict"
    assert (when(read["acquisitionStart"]), when(read["acquisitionEnd"])) == (when(at(10)), when(at(10, 5)))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_must_know_its_zone_and_its_order(authenticated_context: HttpContext):
    naive = await _create(authenticated_context, "naive", [anchor(("2026-10-22T10:00:00", None))])
    assert naive.errors and "time zone" in str(naive.errors[0])
    backwards = await _create(authenticated_context, "backwards", [anchor((at(11), at(10)))])
    assert backwards.errors and "before it starts" in str(backwards.errors[0])


# --- filtering --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_filter_finds_acquisitions_overlapping_the_window(authenticated_context: HttpContext):
    inside = (await _created(authenticated_context, "inside", [anchor((at(10, 10), at(10, 20)))]))["id"]
    straddling = (await _created(authenticated_context, "straddling", [anchor((at(9), at(10, 5)))]))["id"]
    before = (await _created(authenticated_context, "before", [anchor((at(8), at(9)))]))["id"]
    after = (await _created(authenticated_context, "after", [anchor((at(12), at(13)))]))["id"]
    untimed = (await _created(authenticated_context, "untimed", [anchor(None)]))["id"]

    found = await _listed(authenticated_context, {"acquiredAfter": at(10), "acquiredBefore": at(11)})
    assert {inside, straddling} <= found
    assert not {before, after, untimed} & found

    ordered = await schema.execute(
        "query { arrayDatasets(filters: {acquiredAfter: \"%s\"}, ordering: [{acquisitionStart: ASC}]) { id } }" % at(0), context_value=authenticated_context
    )
    assert not ordered.errors, ordered.errors
    assert [dataset["id"] for dataset in ordered.data["arrayDatasets"]] == [before, straddling, inside, after]


# --- the abstraction --------------------------------------------------------------------


def _said(value, anchor_id: int = 1, **coordinates: int) -> Statement:
    return Statement(anchor_id, coordinates, value)


def test_covers_is_holding_over():
    assert derived.covers({}, {"t": 3})
    assert derived.covers({"t": 3}, {"t": 3, "c": 0})
    assert not derived.covers({"t": 3}, {"t": 4, "c": 0})
    assert not derived.covers({"t": 3}, {"t": 3}), "an anchor does not hold over itself"


def test_siblings_that_disagree_are_ambiguous_and_a_covering_mismatch_is_a_conflict():
    assert isinstance(agreement([_said("63x", 1, c=0), _said("40x", 2, c=1)]), Ambiguous)
    verdict = agreement([_said("63x", 1), _said("40x", 2, c=0)])
    assert isinstance(verdict, Conflict) and verdict.anchors == (1, 2)
    assert agreement([_said("63x", 1), _said("63x", 2, c=0)]) == Stated("63x")
    assert agreement([]) is derived.UNSTATED


def test_an_envelope_is_never_ambiguous():
    assert envelope([_said((1, 2), 1, t=0), _said((5, 9), 2, t=1)]) == Stated((1, 9))
    assert isinstance(envelope([_said((1, 5), 1), _said((4, 6), 2, t=0)]), Conflict)


def test_a_derived_field_migrates_as_the_plain_field_and_names_its_deriver():
    field = models.ArrayDataset._meta.get_field("acquisition_start")
    _, path, _, kwargs = field.deconstruct()
    assert path == "django.db.models.DateTimeField"
    assert "deriver" not in kwargs and "help_text" not in kwargs
    assert "core.logic.acquisition.start" in field.help_text
    assert type(field.clone()) is models.ArrayDataset._meta.get_field("created_at").__class__


# --- many anchors at once ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_per_timepoint_is_derived_once(authenticated_context: HttpContext):
    """Ingest states a window per timepoint: the dataset is derived once for all of them, not once per window."""
    from asgiref.sync import sync_to_async

    frames = [anchor((at(10, minute), None), t=minute) for minute in range(50)]
    with patch.object(derived, "rederive", wraps=derived.rederive) as rederive:
        created_many = await _created(authenticated_context, "many", [anchor((at(10), at(10, 49))), *frames])
    assert rederive.call_count == 1, "the windows are read once per dataset, however many timepoints state one"

    read = await _read(authenticated_context, created_many["id"])
    assert (when(read["acquisitionStart"]), when(read["acquisitionEnd"])) == (when(at(10)), when(at(10, 49)))
    assert read["derivationConflicts"] == []
    assert await sync_to_async(models.AcquisitionWindow.objects.filter(anchor__dataset_id=created_many["id"]).count)() == 51


def test_deferred_derives_once_at_the_outermost_close_and_not_at_all_on_error():
    derived_rows: list[object] = []

    class Row:
        pk = 1

    with patch.object(derived, "rederive", side_effect=derived_rows.append):
        row = Row()
        with derived.deferred():
            derived.request(row)
            with derived.deferred():
                derived.request(row)
            assert derived_rows == [], "an inner close derives nothing"
        assert derived_rows == [row]

        derived_rows.clear()
        with pytest.raises(RuntimeError), derived.deferred():
            derived.request(row)
            raise RuntimeError("rolled back")
        assert derived_rows == []

        derived.request(row)
        assert derived_rows == [row], "outside a block, a request derives at once"
