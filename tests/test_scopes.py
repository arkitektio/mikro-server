"""What an app may do here is what its token's scopes say: every root field asks for one of mikro's
own (the ``@auth`` directive publishes it, a generated client gathers it), and `mikro_all` stands
for all of them."""

import pytest
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from arkitekt_service.graphql import scope_gaps
from mikro_server.schema import schema

MAKE_FOLDER = 'mutation { createFolder(input: {name: "scoped"}) { id } }'
FOLDERS = "query { folders { id } }"


def _as(token: str) -> HttpContext:
    return HttpContext(request=UniversalRequest(_extensions={}), response=TemporalResponse(), headers={"Authorization": f"Bearer {token}"}, type="http")


def _reason(result) -> str | None:
    return result.errors[0].extensions.get("reason") if result.errors else None


def test_every_root_field_asks_for_a_scope_mikro_declares():
    assert scope_gaps(schema) == []


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_an_app_granted_reads_reads_and_writes_nothing(backend_stack):
    assert (await schema.execute(FOLDERS, context_value=_as("readertest"))).errors is None
    refused = await schema.execute(MAKE_FOLDER, context_value=_as("readertest"))
    assert _reason(refused) == "INSUFFICIENT_SCOPE"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_the_all_scope_writes(backend_stack):
    made = await schema.execute(MAKE_FOLDER, context_value=_as("test"))
    assert made.errors is None, made.errors
