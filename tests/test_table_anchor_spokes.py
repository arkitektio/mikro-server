"""Coordinate anchors on a table dataset.

A table's rows are scientific records, but the facts about how they were measured -- the
microscope state, the OME block, a channel's label -- are the same facts an image carries,
and they live in the same place: a ``CoordinateAnchor``, pinned to values of the table's
coordinate columns rather than to pixel indices. One hub, one set of spokes, two kinds of
container; the anchor's check constraint keeps it exactly one of the two.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id }
}
"""

TABLE_ANCHORS = """
query Anchors($id: ID!) {
  tableDataset(id: $id) {
    anchors {
      id
      coordinates
      table { id }
      dataset { id }
      channelLabel { label }
      microscope { state { stage { x } } }
    }
  }
}
"""

LIST_ANCHORS = """
query List($table: ID!) {
  coordinateAnchors(filters: { table: { exact: $table } }) { id coordinates }
}
"""

CREATE_ANCHOR = """
mutation Attach($input: CreateCoordinateAnchorInput!) {
  createCoordinateAnchor(input: $input) { id coordinates table { id } channelLabel { label } omeMetadata { id } }
}
"""

DELETE_TABLE = """
mutation Delete($input: DeleteTableDatasetInput!) { deleteTableDataset(input: $input) }
"""

#: A localization table: two SPACE coordinate columns and one measurement.
LOCALIZATIONS = [
    {"name": "x", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "SPACE"},
    {"name": "y", "dtype": "BIGINT", "role": "COORDINATE", "axisType": "SPACE"},
    {"name": "intensity", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]

#: A pure measurement table: no coordinate columns, so its space is the synthetic INDEX axis.
MEASUREMENTS = [
    {"name": "area", "dtype": "DOUBLE", "role": "ATTRIBUTE"},
]

MICROSCOPE = {"stage": {"x": "100.5 um", "y": "-3.25 um"}, "temperature": "37 degC", "devices": []}


async def _create(ctx: HttpContext, name: str, columns: list[dict], anchors: list[dict] | None = None):
    payload = await seed.table_input(ctx, name, columns)
    if anchors is not None:
        payload["anchors"] = anchors
    return await schema.execute(CREATE_TABLE, context_value=ctx, variable_values={"input": payload})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_anchor_pins_spokes_to_its_coordinate_columns(authenticated_context: HttpContext):
    """Anchors stated at create land on the table with the dataset side empty."""
    result = await _create(
        authenticated_context,
        "Anchored",
        LOCALIZATIONS,
        anchors=[
            {"axisAnchors": [{"axis": "x", "value": 3}], "label": {"label": "GFP"}, "microscope": MICROSCOPE},
            {"axisAnchors": [], "omeMetadata": {"metadataString": '{"Instrument": "confocal"}'}},
        ],
    )
    assert result.errors is None, result.errors
    table_id = int(result.data["createTableDataset"]["id"])

    anchors = [anchor async for anchor in models.CoordinateAnchor.objects.filter(table_id=table_id).order_by("pk")]
    assert [anchor.coordinates for anchor in anchors] == [{"x": 3}, {}]
    assert all(anchor.dataset_id is None for anchor in anchors)
    assert all(anchor.organization_id == authenticated_context.request.organization.pk for anchor in anchors)

    label = await models.ChannelLabel.objects.aget(anchor__table_id=table_id)
    assert label.label == "GFP"
    assert await models.OptikitState.objects.filter(anchor__table_id=table_id).acount() == 1
    ome = await models.OmeMetadata.objects.aget(anchor__table_id=table_id)
    assert ome.metadata == {"Instrument": "confocal"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_reads_its_anchors_back(authenticated_context: HttpContext, other_org_context: HttpContext):
    """`TableDataset.anchors` and the `coordinateAnchors` list both serve them, within the tenant."""
    result = await _create(
        authenticated_context,
        "Readable",
        LOCALIZATIONS,
        anchors=[{"axisAnchors": [{"axis": "y", "value": 1}], "label": {"label": "DAPI"}, "microscope": MICROSCOPE}],
    )
    assert result.errors is None, result.errors
    table_id = result.data["createTableDataset"]["id"]

    read = await schema.execute(TABLE_ANCHORS, context_value=authenticated_context, variable_values={"id": table_id})
    assert read.errors is None, read.errors
    [anchor] = read.data["tableDataset"]["anchors"]
    assert anchor["coordinates"] == {"y": 1}
    assert anchor["table"] == {"id": table_id}
    assert anchor["dataset"] is None
    assert anchor["channelLabel"] == {"label": "DAPI"}
    assert anchor["microscope"]["state"]["stage"]["x"] is not None

    listed = await schema.execute(LIST_ANCHORS, context_value=authenticated_context, variable_values={"table": table_id})
    assert listed.errors is None, listed.errors
    assert [entry["coordinates"] for entry in listed.data["coordinateAnchors"]] == [{"y": 1}]

    foreign = await schema.execute(LIST_ANCHORS, context_value=other_org_context, variable_values={"table": table_id})
    assert foreign.errors is None, foreign.errors
    assert foreign.data["coordinateAnchors"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_must_name_a_coordinate_column(authenticated_context: HttpContext):
    """A key that is not one of the table's axis-typed columns is refused, and nothing is written."""
    result = await _create(authenticated_context, "Mispinned", LOCALIZATIONS, anchors=[{"axisAnchors": [{"axis": "z", "value": 0}], "label": {"label": "nope"}}])
    assert result.errors is not None
    assert "['z']" in result.errors[0].message and "['x', 'y']" in result.errors[0].message
    assert not await models.TableDataset.objects.filter(name="Mispinned").aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_without_coordinate_columns_only_takes_global_anchors(authenticated_context: HttpContext):
    """The synthetic `object` axis is not a column: nothing can be pinned along it."""
    refused = await _create(authenticated_context, "Flat", MEASUREMENTS, anchors=[{"axisAnchors": [{"axis": "object", "value": 0}], "label": {"label": "nope"}}])
    assert refused.errors is not None
    assert "only be global" in refused.errors[0].message

    accepted = await _create(authenticated_context, "Flat", MEASUREMENTS, anchors=[{"axisAnchors": [], "label": {"label": "whole table"}}])
    assert accepted.errors is None, accepted.errors
    anchor = await models.CoordinateAnchor.objects.aget(table_id=accepted.data["createTableDataset"]["id"])
    assert anchor.coordinates == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_phasor_spoke_is_array_only(authenticated_context: HttpContext):
    """A phasor is the DFT along an array axis; a table has none, so the spoke is refused."""
    result = await _create(
        authenticated_context,
        "Phasored",
        LOCALIZATIONS,
        anchors=[{"axisAnchors": [], "phasorHistogram": {"axis": "tau", "counts": [1.0, 0.0, 0.0, 0.0], "bins": 2}}],
    )
    assert result.errors is not None
    assert "array-only" in result.errors[0].message
    assert not await models.TableDataset.objects.filter(name="Phasored").aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_metadata_is_attached_to_an_existing_table_on_one_anchor(authenticated_context: HttpContext):
    """`createCoordinateAnchor` get-or-creates on (table, coordinates) and replaces a restated spoke."""
    created = await _create(authenticated_context, "Later", LOCALIZATIONS)
    assert created.errors is None, created.errors
    table_id = created.data["createTableDataset"]["id"]

    first = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"table": table_id, "anchor": {"axisAnchors": [{"axis": "x", "value": 3}], "label": {"label": "first"}}}},
    )
    assert first.errors is None, first.errors
    second = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"table": table_id, "anchor": {"axisAnchors": [{"axis": "x", "value": 3}], "label": {"label": "second"}, "omeMetadata": {"metadataString": "{}"}}}},
    )
    assert second.errors is None, second.errors

    assert first.data["createCoordinateAnchor"]["id"] == second.data["createCoordinateAnchor"]["id"]
    assert second.data["createCoordinateAnchor"]["table"] == {"id": table_id}
    assert await models.CoordinateAnchor.objects.filter(table_id=table_id).acount() == 1
    label = await models.ChannelLabel.objects.aget(anchor__table_id=table_id)
    assert label.label == "second"
    assert await models.OmeMetadata.objects.filter(anchor__table_id=table_id).acount() == 1

    unknown = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"table": table_id, "anchor": {"axisAnchors": [{"axis": "z", "value": 0}], "label": {"label": "nope"}}}},
    )
    assert unknown.errors is not None and "['z']" in unknown.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_metadata_is_attached_to_an_existing_array_dataset_too(authenticated_context: HttpContext):
    """The same mutation serves an array dataset, checked against its axes."""
    dataset = await seed.create_array_dataset(authenticated_context, "Array")

    result = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.pk), "anchor": {"axisAnchors": [{"axis": "c", "value": 1}], "label": {"label": "mCherry"}}}},
    )
    assert result.errors is None, result.errors
    anchor = await models.CoordinateAnchor.objects.select_related("channel_label").aget(dataset=dataset)
    assert anchor.coordinates == {"c": 1}
    assert anchor.table_id is None
    assert anchor.organization_id == dataset.organization_id
    assert anchor.channel_label.label == "mCherry"

    both = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"dataset": str(dataset.pk), "table": "1", "anchor": {"axisAnchors": [], "label": {"label": "nope"}}}},
    )
    assert both.errors is not None and "exactly one" in both.errors[0].message
    neither = await schema.execute(CREATE_ANCHOR, context_value=authenticated_context, variable_values={"input": {"anchor": {"axisAnchors": [], "label": {"label": "nope"}}}})
    assert neither.errors is not None and "exactly one" in neither.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_table_takes_its_anchors_with_it(authenticated_context: HttpContext):
    """An anchor is part of its table: cascade, spokes included."""
    created = await _create(authenticated_context, "Doomed", LOCALIZATIONS, anchors=[{"axisAnchors": [], "label": {"label": "gone"}}])
    assert created.errors is None, created.errors
    table_id = created.data["createTableDataset"]["id"]
    assert await models.ChannelLabel.objects.filter(anchor__table_id=table_id).acount() == 1

    deleted = await schema.execute(DELETE_TABLE, context_value=authenticated_context, variable_values={"input": {"id": table_id}})
    assert deleted.errors is None, deleted.errors
    assert await models.CoordinateAnchor.objects.filter(table_id=table_id).acount() == 0
    assert await models.ChannelLabel.objects.filter(anchor__table_id=table_id).acount() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_has_exactly_one_container(authenticated_context: HttpContext):
    """The database refuses an anchor with neither container or both, and fills the organization itself."""
    dataset = await seed.create_array_dataset(authenticated_context, "Constrained")
    created = await _create(authenticated_context, "Constrained", LOCALIZATIONS)
    assert created.errors is None, created.errors
    table = await models.TableDataset.objects.aget(pk=created.data["createTableDataset"]["id"])
    organization = authenticated_context.request.organization

    def neither():
        with transaction.atomic():
            models.CoordinateAnchor.objects.create(coordinates={}, organization=organization)

    def both():
        with transaction.atomic():
            models.CoordinateAnchor.objects.create(dataset=dataset, table=table, coordinates={})

    with pytest.raises(IntegrityError):
        await sync_to_async(neither)()
    with pytest.raises(IntegrityError):
        await sync_to_async(both)()

    plain = await sync_to_async(models.CoordinateAnchor.objects.create)(dataset=dataset, coordinates={"c": 0})
    assert plain.organization_id == dataset.organization_id
    on_table = await sync_to_async(models.CoordinateAnchor.objects.create)(table=table, coordinates={})
    assert on_table.organization_id == table.organization_id
