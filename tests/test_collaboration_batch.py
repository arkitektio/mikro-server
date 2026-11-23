"""`addManyToCollaboration`: a dataset goes in with what was derived from it, in one go, all or none.

The client walks `descendants` and names every object; this puts them in, in the order named, each
by hand (not brought), each with what it needs to be seen. The collaboration is lok's, named by its
key: the batch makes mikro's row of it, or, refused, leaves none behind.
"""

import uuid

import pytest
from arkitekt_service.collaborations.models import Collaboration, CollaborationItem
from asgiref.sync import sync_to_async
from django.db.models import Model
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from mikro_server.schema import schema
from tests import seed

ADD_MANY = """
mutation ($input: AddManyToCollaborationInput!) {
  addManyToCollaboration(input: $input) { key items { identifier objectId brought } }
}
"""


async def _run(ctx: HttpContext, query: str, **variables: object) -> ExecutionResult:
    return await schema.execute(
        query, context_value=ctx, variable_values=variables or None
    )


def _ref(identifier: str, obj: Model) -> dict:
    return {"identifier": identifier, "object": str(obj.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_object_named_goes_in_once_and_by_hand(
    authenticated_context: HttpContext,
) -> None:
    raw = await seed.create_array_dataset(authenticated_context, "raw")
    mask = await seed.create_array_dataset(authenticated_context, "mask")
    key = str(uuid.uuid4())

    result = await _run(
        authenticated_context,
        ADD_MANY,
        input={
            "collaboration": key,
            "objects": [
                _ref("@mikro/arraydataset", raw),
                _ref("@mikro/arraydataset", mask),
                _ref("@mikro/arraydataset", raw),
            ],
        },
    )

    assert not result.errors, result.errors
    assert result.data is not None
    assert result.data["addManyToCollaboration"]["key"] == key
    put = [
        (item["objectId"], item["brought"])
        for item in result.data["addManyToCollaboration"]["items"]
        if item["identifier"] == "@mikro/arraydataset"
    ]
    assert put == [(str(raw.pk), False), (str(mask.pk), False)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_object_that_cannot_go_in_keeps_the_whole_batch_out(
    authenticated_context: HttpContext,
) -> None:
    raw = await seed.create_array_dataset(authenticated_context, "raw")
    key = str(uuid.uuid4())

    result = await _run(
        authenticated_context,
        ADD_MANY,
        input={
            "collaboration": key,
            "objects": [
                _ref("@mikro/arraydataset", raw),
                {"identifier": "@mikro/arraydataset", "object": "999999"},
            ],
        },
    )

    assert result.errors
    assert not await sync_to_async(
        CollaborationItem.objects.filter(collaboration__key=key).exists
    )()
    assert not await sync_to_async(Collaboration.objects.filter(key=key).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_part_goes_in_after_its_whole(
    authenticated_context: HttpContext,
) -> None:
    dataset = await seed.create_array_dataset(authenticated_context, "raw")
    lens = await seed.create_lens(authenticated_context, dataset)
    key = str(uuid.uuid4())

    early = await _run(
        authenticated_context,
        ADD_MANY,
        input={
            "collaboration": key,
            "objects": [
                _ref("@mikro/arraylens", lens),
                _ref("@mikro/arraydataset", dataset),
            ],
        },
    )
    assert early.errors, "a part was put in before its whole"

    ordered = await _run(
        authenticated_context,
        ADD_MANY,
        input={
            "collaboration": key,
            "objects": [
                _ref("@mikro/arraydataset", dataset),
                _ref("@mikro/arraylens", lens),
            ],
        },
    )
    assert not ordered.errors, ordered.errors
