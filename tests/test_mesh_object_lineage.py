"""One object of a mesh collection can be asked what was made from it.

Data computed from one surface -- a crop to a cell's mesh, a table measured over one
organelle -- names it as a MESH_OBJECT source: an edge into the collection's vertex space
that carries the collection and the object's catalog id, read from the other end as
`meshObject(collection:, object:).derivedResidents`. The ANNOTATION arm's twin, for a
part of a collection that is a Parquet row rather than a row here.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request
from tests.test_derived_datasets import _derive
from tests.test_layer_kinds import _seed_mesh_collection_sync

MESH_OBJECT = """
query MeshObject($collection: ID!, $object: Int!) {
  meshObject(collection: $collection, object: $object) {
    id
    objectId
    collection { id }
    derivedResidents { __typename ... on ArrayDataset { id name } }
  }
}
"""

DERIVED_FROM = """
query DerivedFrom($id: ID!) {
  arrayDataset(id: $id) {
    derivedFrom { kind output { id } }
  }
}
"""


async def _mesh_object(ctx: HttpContext, collection: models.MeshCollection, object_id: int) -> dict:
    result = await schema.execute(
        MESH_OBJECT, context_value=_fresh_request(ctx), variable_values={"collection": str(collection.pk), "object": object_id}
    )
    assert not result.errors, result.errors
    return result.data["meshObject"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_names_the_mesh_object_it_was_computed_from(authenticated_context: HttpContext):
    ctx = authenticated_context
    collection = await sync_to_async(_seed_mesh_collection_sync)(ctx)
    system = await sync_to_async(lambda: collection.coordinate_system)()
    source = await seed.create_array_dataset(ctx, "Raw", axes=seed.YX_AXES, shapes=[[6, 8]])
    lens = await seed.create_lens(ctx, source, slices=[])

    made = await _derive(
        ctx,
        "Cell 7",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk), "meshObject": 7},
        ],
    )
    assert not made.errors, made.errors
    cell = made.data["createArrayDataset"]["id"]

    derived = await schema.execute(DERIVED_FROM, context_value=_fresh_request(ctx), variable_values={"id": cell})
    assert not derived.errors, derived.errors
    _, by_object = derived.data["arrayDataset"]["derivedFrom"]
    assert by_object["kind"] == "UNMAPPABLE", "naming an object claims no geometry"
    assert by_object["output"]["id"] == str(system.pk), "it lands where a collection derivation lands: the vertex space"

    seven = await _mesh_object(ctx, collection, 7)
    assert seven["id"] == f"{collection.pk}:7"
    assert seven["derivedResidents"] == [{"__typename": "ArrayDataset", "id": cell, "name": "Cell 7"}]
    assert (await _mesh_object(ctx, collection, 8))["derivedResidents"] == [], "the collection is shared, the lineage is the object's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_objects_of_one_collection_are_two_sources(authenticated_context: HttpContext):
    ctx = authenticated_context
    collection = await sync_to_async(_seed_mesh_collection_sync)(ctx)
    source = await seed.create_array_dataset(ctx, "Raw", axes=seed.YX_AXES, shapes=[[6, 8]])
    lens = await seed.create_lens(ctx, source, slices=[])

    both = await _derive(
        ctx,
        "Contact",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk), "meshObject": 1},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk), "meshObject": 2},
        ],
    )
    assert not both.errors, both.errors
    contact = both.data["createArrayDataset"]["id"]
    assert [r["id"] for r in (await _mesh_object(ctx, collection, 1))["derivedResidents"]] == [contact]
    assert [r["id"] for r in (await _mesh_object(ctx, collection, 2))["derivedResidents"]] == [contact]

    twice = await _derive(
        ctx,
        "Twice",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk), "meshObject": 1},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk), "meshObject": 1},
        ],
    )
    assert twice.errors and "distinct source" in str(twice.errors[0]), "one object named twice is one source"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_object_arm_needs_its_object(authenticated_context: HttpContext):
    ctx = authenticated_context
    collection = await sync_to_async(_seed_mesh_collection_sync)(ctx)
    source = await seed.create_array_dataset(ctx, "Raw", axes=seed.YX_AXES, shapes=[[6, 8]])
    lens = await seed.create_lens(ctx, source, slices=[])
    made = await _derive(
        ctx,
        "Nothing",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "MESH_OBJECT", "meshCollection": str(collection.pk)},
        ],
    )
    assert made.errors, "a MESH_OBJECT entry without its object is refused"
