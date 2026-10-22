"""`addManyToCollaboration`: a dataset goes in with what was derived from it, in one go, all or none.

The client walks `descendants` and names every object; this puts them in, in the order named, each
by hand (not brought), each with what it needs to be seen.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from arkitekt_service.collaborations.models import Collaboration, CollaborationItem
from mikro_server.schema import schema
from tests import seed

ADD_MANY = """
mutation ($input: AddManyToCollaborationInput!) {
  addManyToCollaboration(input: $input) { id items { identifier objectId brought } }
}
"""


async def _run(ctx: HttpContext, query: str, **variables):
    return await schema.execute(query, context_value=ctx, variable_values=variables or None)


async def _empty(ctx: HttpContext) -> Collaboration:
    return await sync_to_async(Collaboration.objects.create)(organization=ctx.request.organization, name="Shared")


def _ref(identifier: str, obj) -> dict:
    return {"identifier": identifier, "object": str(obj.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_object_named_goes_in_once_and_by_hand(authenticated_context):
    raw = await seed.create_array_dataset(authenticated_context, "raw")
    mask = await seed.create_array_dataset(authenticated_context, "mask")
    collaboration = await _empty(authenticated_context)

    result = await _run(
        authenticated_context,
        ADD_MANY,
        input={"collaboration": str(collaboration.pk), "objects": [_ref("@mikro/arraydataset", raw), _ref("@mikro/arraydataset", mask), _ref("@mikro/arraydataset", raw)]},
    )

    assert not result.errors, result.errors
    put = [(item["objectId"], item["brought"]) for item in result.data["addManyToCollaboration"]["items"] if item["identifier"] == "@mikro/arraydataset"]
    assert put == [(str(raw.pk), False), (str(mask.pk), False)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_object_that_cannot_go_in_keeps_the_whole_batch_out(authenticated_context):
    raw = await seed.create_array_dataset(authenticated_context, "raw")
    collaboration = await _empty(authenticated_context)

    result = await _run(
        authenticated_context,
        ADD_MANY,
        input={"collaboration": str(collaboration.pk), "objects": [_ref("@mikro/arraydataset", raw), {"identifier": "@mikro/arraydataset", "object": "999999"}]},
    )

    assert result.errors
    assert not await sync_to_async(CollaborationItem.objects.filter(collaboration=collaboration).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_part_goes_in_after_its_whole(authenticated_context):
    dataset = await seed.create_array_dataset(authenticated_context, "raw")
    lens = await seed.create_lens(authenticated_context, dataset)
    collaboration = await _empty(authenticated_context)

    early = await _run(authenticated_context, ADD_MANY, input={"collaboration": str(collaboration.pk), "objects": [_ref("@mikro/arraylens", lens), _ref("@mikro/arraydataset", dataset)]})
    assert early.errors, "a part was put in before its whole"

    ordered = await _run(authenticated_context, ADD_MANY, input={"collaboration": str(collaboration.pk), "objects": [_ref("@mikro/arraydataset", dataset), _ref("@mikro/arraylens", lens)]})
    assert not ordered.errors, ordered.errors
