"""Read grants are handed to a caller of an organization, and to nobody else."""

import pytest
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse

from mikro_server.schema import schema

GENERAL = "mutation { requestGeneralZarrAccess(input: {}) { __typename } }"
ONE = 'mutation { requestZarrAccess(input: {storeId: "1"}) { __typename } }'


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("document", [GENERAL, ONE], ids=["general", "one-store"])
async def test_a_request_without_a_token_is_handed_no_read_grant(db, backend_stack, document: str):
    anonymous = HttpContext(request=UniversalRequest(_extensions={}), response=TemporalResponse(), headers={}, type="http")

    refused = await schema.execute(document, context_value=anonymous)

    assert refused.data is None
    assert [str(error) for error in refused.errors] == ["No Authorization header was provided."], "refused for the whole operation, before any resolver runs"
