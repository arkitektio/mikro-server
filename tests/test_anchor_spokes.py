"""The coordinate-anchor metadata spokes written at ingest.

The microscope (Optikit) state is the hardware truth -- stage pose, environment,
per-device settings at the moment of acquisition -- and it is a spoke like the
others: pinned to an anchor, written once at ingest, never fabricated later. It
is *typed*, like the lightpath graph: composable input types with quantities, the
model's dump as the stored JSON, and the same model reconstructed on read.
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from datalayer.models import ZarrStore
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from optikit.models import OptikitStateModel


CREATE = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id }
}
"""


async def _store(ctx: HttpContext, key: str) -> ZarrStore:
    return await ZarrStore.objects.acreate(
        organization=ctx.request.organization,
        key=key,
        bucket="zarr",
        shape=[3, 64, 64],
        chunks=[3, 64, 64],
        version="3",
        dtype="uint8",
        populated=True,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_microscope_state_is_written_as_a_typed_anchor_spoke(authenticated_context: HttpContext):
    """Ingest pins the recorded Optikit state to its coordinate, through composable typed input.

    Quantities arrive as unit-carrying strings and are stored canonically; the stored
    JSON is exactly the model's dump, so reconstructing it recovers the same state.
    """
    store = await _store(authenticated_context, "optikit-anchored")

    state_input = {
        "stage": {"x": "100.5 um", "y": "-3.25 um"},
        "temperature": "37 degC",
        "devices": [
            {
                "label": "laser-488",
                "kind": "laser",
                "settings": [
                    {"name": "power", "quantity": "20 mW"},
                    {"name": "shutter", "flag": True},
                ],
            },
            {
                "label": "filter-wheel-1",
                "settings": [{"name": "position", "text": "GFP"}],
            },
        ],
    }

    variables = {
        "input": {
            "data": str(store.id),
            "name": "Acquired",
            "scales": [],
            "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
            "anchors": [
                {
                    "axisAnchors": [{"axis": "c", "value": 0}],
                    "microscope": state_input,
                }
            ],
        }
    }
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(CREATE, context_value=authenticated_context, variable_values=variables)
    assert not result.errors, result.errors

    spoke = await models.OptikitState.objects.aget(anchor__dataset_id=result.data["createArrayDataset"]["id"])
    anchor = await sync_to_async(lambda: spoke.anchor)()
    assert anchor.coordinates == {"c": 0}, "the hardware truth is pinned to the coordinate it was recorded at"

    # The stored JSON is the model's dump: reconstructing it recovers the state the
    # client sent, quantities compared canonically rather than by their spelling.
    stored = OptikitStateModel(**spoke.state)
    sent = OptikitStateModel(**state_input)
    assert stored == sent
    assert stored.devices[0].settings[0].name == "power"
    assert stored.devices[1].settings[0].text == "GFP"

    # And back out through GraphQL: the typed read surface, quantities as scalars.
    from core.models import ArrayDataset
    from tests import seed

    dataset = await ArrayDataset.objects.aget(pk=result.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    read = await schema.execute(
        """
        query Read($id: ID!) {
          lens(id: $id) {
            activeAnchors {
              microscope {
                state {
                  stage { x y }
                  temperature
                  devices { label kind settings { name quantity number text flag } }
                }
              }
            }
          }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(lens.pk)},
    )
    assert not read.errors, read.errors
    (read_anchor,) = read.data["lens"]["activeAnchors"]
    state = read_anchor["microscope"]["state"]
    assert state["stage"]["x"] is not None
    assert state["devices"][0]["label"] == "laser-488"
    assert state["devices"][0]["settings"][0]["quantity"] is not None
    assert state["devices"][1]["settings"][0]["text"] == "GFP"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_setting_holds_exactly_one_value(authenticated_context: HttpContext):
    """A setting filling two value slots is refused: it is two settings."""
    store = await _store(authenticated_context, "optikit-two-values")

    variables = {
        "input": {
            "data": str(store.id),
            "name": "Broken",
            "scales": [],
            "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
            "anchors": [
                {
                    "axisAnchors": [],
                    "microscope": {"devices": [{"label": "laser", "settings": [{"name": "power", "quantity": "20 mW", "number": 20.0}]}]},
                }
            ],
        }
    }
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(CREATE, context_value=authenticated_context, variable_values=variables)
    assert result.errors
    assert "one value" in str(result.errors[0])
