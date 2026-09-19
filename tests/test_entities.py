"""Federation's `_entities` answers for every type in the `_Entity` union.

Resolving the union asks each member's `is_type_of`, so a discriminating
`is_type_of` also sees rows of *other* models. The layer and transformation types
read `obj.kind` there, and did so unguarded: any `_entities` request for a folder
(or a dataset, a file, ...) crashed with "'Folder' object has no attribute 'kind'".
"""

from importlib.metadata import version

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests import seed

ENTITIES = """
query Entities($representations: [_Any!]!) {
  entities: _entities(representations: $representations) {
    __typename
    ... on Folder { id name }
    ... on ArrayDataset { id name }
  }
}
"""


def _kante_scopes_references() -> bool:
    major, minor, *_ = (int(p) for p in version("kante").split(".")[:2])
    return (major, minor) >= (2, 3)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_entities_resolves_types_beside_the_discriminated_ones(db, authenticated_context: HttpContext):
    folder = await seed.create_folder(authenticated_context, "federated")
    dataset = await seed.create_array_dataset(authenticated_context, "federated")

    result = await schema.execute(
        ENTITIES,
        context_value=authenticated_context,
        variable_values={
            "representations": [
                {"__typename": "Folder", "id": str(folder.pk)},
                {"__typename": "ArrayDataset", "id": str(dataset.pk)},
            ]
        },
    )

    assert result.errors is None, result.errors
    assert result.data["entities"] == [
        {"__typename": "Folder", "id": str(folder.pk), "name": "federated"},
        {"__typename": "ArrayDataset", "id": str(dataset.pk), "name": "federated"},
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.skipif(not _kante_scopes_references(), reason="kante < 2.3 answers `_entities` unscoped")
async def test_entities_does_not_answer_for_another_organizations_row(
    db, authenticated_context: HttpContext, other_org_context: HttpContext
):
    theirs = await seed.create_folder(other_org_context, "theirs")

    result = await schema.execute(
        ENTITIES,
        context_value=authenticated_context,
        variable_values={"representations": [{"__typename": "Folder", "id": str(theirs.pk)}]},
    )

    assert result.errors is None, result.errors
    assert result.data["entities"] == [None]
