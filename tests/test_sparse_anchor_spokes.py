"""Coordinate anchors on a sparse dataset.

The third container after the array and the table. A matrix's axes are enumerations, so an
anchor's coordinates are positions along them -- ``{"feature": 12}`` is the twelfth feature,
``{}`` the whole matrix -- and the spokes are the ones every container shares: a per-object
expression matrix carries the microscope state of the recording it was computed from
exactly as the image does. Array-only spokes (the phasors) are refused here as on a table.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

CREATE_SPARSE = """
mutation Create($input: CreateSparseDatasetInput!) {
  createSparseDataset(input: $input) { id }
}
"""

SPARSE_ANCHORS = """
query Anchors($id: ID!) {
  sparseDataset(id: $id) {
    anchors {
      id
      coordinates
      sparse { id }
      dataset { id }
      table { id }
      channelLabel { label }
      microscope { state { stage { x } } }
    }
  }
}
"""

LIST_ANCHORS = """
query List($sparse: ID!) {
  coordinateAnchors(filters: { sparse: { exact: $sparse } }) { id coordinates }
}
"""

CREATE_ANCHOR = """
mutation Attach($input: CreateCoordinateAnchorInput!) {
  createCoordinateAnchor(input: $input) { id coordinates sparse { id } channelLabel { label } omeMetadata { id } }
}
"""

DELETE_SPARSE = """
mutation Delete($input: DeleteSparseDatasetInput!) { deleteSparseDataset(input: $input) }
"""

SHAPE = [40, 12]
MICROSCOPE = {"stage": {"x": "100.5 um", "y": "-3.25 um"}, "temperature": "37 degC", "devices": []}


async def _index_table(ctx: HttpContext, name: str, key: str) -> str:
    """A table one position of an axis identifies a row of: a single INDEX column."""
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": await seed.table_input(ctx, name, [{"name": key, "dtype": "BIGINT", "role": "COORDINATE", "axisType": "INDEX"}])},
    )
    assert result.errors is None, result.errors
    return result.data["createTableDataset"]["id"]


async def _axes(ctx: HttpContext, name: str) -> list[dict]:
    """The matrix's two axes, `object` and `feature`, each identified by a table of its own."""
    objects = await _index_table(ctx, f"{name} objects", "object_id")
    features = await _index_table(ctx, f"{name} features", "feature_id")
    return [
        {"name": "object", "identifiedBy": [{"kind": "TABLE", "table": objects}]},
        {"name": "feature", "identifiedBy": [{"kind": "TABLE", "table": features}]},
    ]


async def _create(ctx: HttpContext, name: str, anchors: list[dict] | None = None):
    store = await seed.create_sparse_store(ctx, f"{name}-store", axes=(0,), shape=SHAPE)
    payload: dict = {"name": name, "store": str(store.pk), "axes": await _axes(ctx, name)}
    if anchors is not None:
        payload["anchors"] = anchors
    return await schema.execute(CREATE_SPARSE, context_value=ctx, variable_values={"input": payload})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_anchor_pins_spokes_to_its_axes(authenticated_context: HttpContext):
    """Anchors stated at create land on the matrix with the other containers empty."""
    result = await _create(
        authenticated_context,
        "Anchored",
        anchors=[
            {"axisAnchors": [{"axis": "feature", "value": 12}], "label": {"label": "GAD1"}, "microscope": MICROSCOPE},
            {"axisAnchors": [], "omeMetadata": {"metadataString": '{"Instrument": "confocal"}'}},
        ],
    )
    assert result.errors is None, result.errors
    sparse_id = int(result.data["createSparseDataset"]["id"])

    anchors = [anchor async for anchor in models.CoordinateAnchor.objects.filter(sparse_id=sparse_id).order_by("pk")]
    assert [anchor.coordinates for anchor in anchors] == [{"feature": 12}, {}]
    assert all(anchor.dataset_id is None and anchor.table_id is None for anchor in anchors)
    assert all(anchor.organization_id == authenticated_context.request.organization.pk for anchor in anchors)

    label = await models.ChannelLabel.objects.aget(anchor__sparse_id=sparse_id)
    assert label.label == "GAD1"
    assert await models.OptikitState.objects.filter(anchor__sparse_id=sparse_id).acount() == 1
    ome = await models.OmeMetadata.objects.aget(anchor__sparse_id=sparse_id)
    assert ome.metadata == {"Instrument": "confocal"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_dataset_reads_its_anchors_back(authenticated_context: HttpContext, other_org_context: HttpContext):
    """`SparseDataset.anchors` and the `coordinateAnchors` list both serve them, within the tenant."""
    result = await _create(authenticated_context, "Readable", anchors=[{"axisAnchors": [{"axis": "object", "value": 7}], "label": {"label": "cell 7"}, "microscope": MICROSCOPE}])
    assert result.errors is None, result.errors
    sparse_id = result.data["createSparseDataset"]["id"]

    read = await schema.execute(SPARSE_ANCHORS, context_value=authenticated_context, variable_values={"id": sparse_id})
    assert read.errors is None, read.errors
    [anchor] = read.data["sparseDataset"]["anchors"]
    assert anchor["coordinates"] == {"object": 7}
    assert anchor["sparse"] == {"id": sparse_id}
    assert anchor["dataset"] is None and anchor["table"] is None
    assert anchor["channelLabel"] == {"label": "cell 7"}
    assert anchor["microscope"]["state"]["stage"]["x"] is not None

    listed = await schema.execute(LIST_ANCHORS, context_value=authenticated_context, variable_values={"sparse": sparse_id})
    assert listed.errors is None, listed.errors
    assert [entry["coordinates"] for entry in listed.data["coordinateAnchors"]] == [{"object": 7}]

    foreign = await schema.execute(LIST_ANCHORS, context_value=other_org_context, variable_values={"sparse": sparse_id})
    assert foreign.errors is None, foreign.errors
    assert foreign.data["coordinateAnchors"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_must_name_an_axis_of_the_matrix(authenticated_context: HttpContext):
    """A key that is not one of the matrix's axes is refused, and nothing is written."""
    result = await _create(authenticated_context, "Mispinned", anchors=[{"axisAnchors": [{"axis": "gene", "value": 0}], "label": {"label": "nope"}}])
    assert result.errors is not None
    assert "['gene']" in result.errors[0].message and "['object', 'feature']" in result.errors[0].message
    assert not await models.SparseDataset.objects.filter(name="Mispinned").aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_phasor_spoke_is_array_only(authenticated_context: HttpContext):
    """A phasor is the DFT along an array axis; a matrix has none, so the spoke is refused."""
    result = await _create(authenticated_context, "Phasored", anchors=[{"axisAnchors": [], "phasorHistogram": {"axis": "tau", "counts": [1.0, 0.0, 0.0, 0.0], "bins": 2}}])
    assert result.errors is not None
    assert "array-only" in result.errors[0].message
    assert not await models.SparseDataset.objects.filter(name="Phasored").aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_metadata_is_attached_to_an_existing_matrix_on_one_anchor(authenticated_context: HttpContext):
    """`createCoordinateAnchor` get-or-creates on (sparse, coordinates) and replaces a restated spoke."""
    created = await _create(authenticated_context, "Later")
    assert created.errors is None, created.errors
    sparse_id = created.data["createSparseDataset"]["id"]

    first = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"sparse": sparse_id, "anchor": {"axisAnchors": [{"axis": "feature", "value": 3}], "label": {"label": "first"}}}},
    )
    assert first.errors is None, first.errors
    second = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"sparse": sparse_id, "anchor": {"axisAnchors": [{"axis": "feature", "value": 3}], "label": {"label": "second"}, "omeMetadata": {"metadataString": "{}"}}}},
    )
    assert second.errors is None, second.errors

    assert first.data["createCoordinateAnchor"]["id"] == second.data["createCoordinateAnchor"]["id"]
    assert second.data["createCoordinateAnchor"]["sparse"] == {"id": sparse_id}
    assert await models.CoordinateAnchor.objects.filter(sparse_id=sparse_id).acount() == 1
    label = await models.ChannelLabel.objects.aget(anchor__sparse_id=sparse_id)
    assert label.label == "second"
    assert await models.OmeMetadata.objects.filter(anchor__sparse_id=sparse_id).acount() == 1

    unknown = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"sparse": sparse_id, "anchor": {"axisAnchors": [{"axis": "gene", "value": 0}], "label": {"label": "nope"}}}},
    )
    assert unknown.errors is not None and "['gene']" in unknown.errors[0].message

    two = await schema.execute(
        CREATE_ANCHOR,
        context_value=authenticated_context,
        variable_values={"input": {"sparse": sparse_id, "table": "1", "anchor": {"axisAnchors": [], "label": {"label": "nope"}}}},
    )
    assert two.errors is not None and "exactly one" in two.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_the_matrix_takes_its_anchors_with_it(authenticated_context: HttpContext):
    """An anchor is part of its matrix: cascade, spokes included."""
    created = await _create(authenticated_context, "Doomed", anchors=[{"axisAnchors": [], "label": {"label": "gone"}}])
    assert created.errors is None, created.errors
    sparse_id = created.data["createSparseDataset"]["id"]
    assert await models.ChannelLabel.objects.filter(anchor__sparse_id=sparse_id).acount() == 1

    deleted = await schema.execute(DELETE_SPARSE, context_value=authenticated_context, variable_values={"input": {"id": sparse_id}})
    assert deleted.errors is None, deleted.errors
    assert await models.CoordinateAnchor.objects.filter(sparse_id=sparse_id).acount() == 0
    assert await models.ChannelLabel.objects.filter(anchor__sparse_id=sparse_id).acount() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anchor_has_exactly_one_container(authenticated_context: HttpContext):
    """The database refuses an anchor on a matrix and a dataset at once, and fills the organization itself."""
    dataset = await seed.create_array_dataset(authenticated_context, "Constrained")
    created = await _create(authenticated_context, "Constrained")
    assert created.errors is None, created.errors
    matrix = await models.SparseDataset.objects.aget(pk=created.data["createSparseDataset"]["id"])

    def both():
        with transaction.atomic():
            models.CoordinateAnchor.objects.create(dataset=dataset, sparse=matrix, coordinates={})

    with pytest.raises(IntegrityError):
        await sync_to_async(both)()

    on_matrix = await sync_to_async(models.CoordinateAnchor.objects.create)(sparse=matrix, coordinates={"feature": 0})
    assert on_matrix.organization_id == matrix.organization_id
    assert (await sync_to_async(lambda: on_matrix.container)()) == matrix
