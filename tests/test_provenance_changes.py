"""`effectiveChanges` must survive a field whose value is not a string.

``diff_against`` yields the *raw* field values -- a JSONField's list, a dict, an FK's model
instance -- and both ``oldValue`` and ``newValue`` are declared ``String``. Handing a list
to a String scalar is not a null, it is a ``GraphQLError`` that fails the whole query, so a
single JSON column made every provenance read on that model unanswerable.

``ArrayDataset.stored_spec`` is the one that bit, and the way it does is worth stating: the row
is created with it empty, and the axis writer materializes it through
``save_without_historical_record`` (``core/logic/graph.py``) -- so the *first* history row
still says ``[]`` while the table says ``['IMAGE', ...]``. The next save of that dataset,
whatever it was for, writes a row that diffs against the stale one and reports a change
nobody made. A rename is enough; so is re-filing it.
"""

from unittest.mock import patch

import pytest
from datalayer.models import ZarrStore
from kante.context import HttpContext

from mikro_server.schema import schema

PROVENANCE = """
query Provenance($id: ID!) {
  arrayDataset(id: $id) {
    provenanceEntries {
      effectiveChanges { field oldValue newValue oldValueJson newValueJson }
    }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_json_field_change_does_not_break_the_provenance_query(authenticated_context: HttpContext):
    """A list-valued change reads back as a string, and keeps its native type in the JSON twin."""
    ctx = authenticated_context
    store = await ZarrStore.objects.acreate(
        organization=ctx.request.organization,
        key="provenance",
        bucket="zarr",
        shape=[3, 32, 32],
        chunks=[3, 32, 32],
        version="3",
        dtype="uint8",
        populated=True,
    )

    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        created = await schema.execute(
            "mutation Create($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }",
            context_value=ctx,
            variable_values={
                "input": {
                    "name": "Provenanced",
                    "data": str(store.pk),
                    "scales": [],
                    "axes": [
                        {"name": "c", "type": "CHANNEL"},
                        {"name": "y", "type": "SPACE"},
                        {"name": "x", "type": "SPACE"},
                    ],
                }
            },
        )
    assert not created.errors, created.errors
    assert created.data
    dataset_id = created.data["createArrayDataset"]["id"]

    # Any second save is enough; a rename is the plainest one and predates every folder
    # change, so this reproduces the failure on its own terms.
    renamed = await schema.execute(
        "mutation Update($input: UpdateArrayDatasetInput!) { updateArrayDataset(input: $input) { id name } }",
        context_value=ctx,
        variable_values={"input": {"id": dataset_id, "name": "Renamed"}},
    )
    assert not renamed.errors, renamed.errors

    result = await schema.execute(PROVENANCE, context_value=ctx, variable_values={"id": dataset_id})
    assert not result.errors, result.errors
    assert result.data

    changes = [change for entry in result.data["arrayDataset"]["provenanceEntries"] for change in entry["effectiveChanges"]]
    spec = [change for change in changes if change["field"] == "stored_spec"]
    assert spec, "the axis writer materializes stored_spec after creation, so the history has this change"

    change = spec[-1]
    assert isinstance(change["newValue"], str), "String fields must receive a string, whatever the column holds"
    assert change["newValueJson"] == ["IMAGE", "MULTICHANNEL"], "the JSON twin keeps the native type a client can actually use"
