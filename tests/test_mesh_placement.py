"""A mesh layer must reach world, before and after the collection owns its own system.

Nothing covered this. `createMeshLayer` builds a layer over the *legacy* `Mesh` (a
BigFileStore), so a layer over a `MeshCollection` can only be authored through the ORM --
and its placement, which runs through `layer_source_system` -> `mesh_collection
.coordinate_system`, had no test at all.

That matters right now, because the collection is about to stop *borrowing* the dataset's
intrinsic system and start *owning* one, anchored to the dataset by an edge. If the
placement search cannot resolve the collection's own system back to its dataset, a mesh
layer silently loses its path to world and every existing test still passes. So this file
is written first, against the old shape, and must stay green through the change.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers {
      id
      pathToWorld {
        inverted
        transformation { id kind input { id  } output { id residents { __typename } } }
      }
    }
  }
}
"""

_AFFINE_3D = [
    [1.0, 0.0, 0.0, 5.0],
    [0.0, 1.0, 0.0, 5.0],
    [0.0, 0.0, 1.0, 0.0],
]


async def _mesh_collection(ctx: HttpContext, dataset: models.ArrayDataset) -> models.MeshCollection:
    """A collection over a dataset's meshes, through the real mutation."""

    store = await seed.create_fabriks_store(ctx)
    system = await sync_to_async(lambda: dataset.intrinsic_coordinate_system)()
    axes = await sync_to_async(lambda: [{"name": axis.name, "type": axis.type} for axis in system.axes.all()])()

    result = await schema.execute(
        """
        mutation Create($input: CreateMeshCollectionInput!) {
          createMeshCollection(input: $input) { id coordinateSystem { id  } }
        }
        """,
        context_value=ctx,
        variable_values={
            "input": {
                # The axes the meshes' vertices are in -- required now, and stated as the
                # source's because this fixture's meshes are expressed in that grid as-is,
                # which is what the IDENTITY below says.
                "axes": axes,
                "derivedFrom": [{"kind": "COORDINATE_SYSTEM", "coordinateSystem": str(system.pk), "transform": {"kind": "IDENTITY"}}],
                "version": "v1",
                "store": str(store.pk),
            }
        },
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.MeshCollection.objects.get)(pk=result.data["createMeshCollection"]["id"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mesh_layer_reaches_world(authenticated_context: HttpContext):
    """The meshes were extracted from a dataset, so they go where that dataset went."""
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")
    collection = await _mesh_collection(authenticated_context, dataset)
    scene = await seed.create_scene(authenticated_context, "Composition")

    def place() -> None:
        models.Transformation.objects.create(
            kind=enums.TransformKindChoices.AFFINE.value,
            input=dataset.intrinsic_coordinate_system,
            output=scene.world,
            params={"affine": _AFFINE_3D},
            organization=authenticated_context.request.organization,
        )
        # Written through the ORM rather than `createMeshLayer`, which would run its own
        # placement gate and refuse before this query could observe the path. That gate is
        # the point of the mutation; this file is about what the *read* side computes.
        models.Layer.objects.create(kind=enums.LayerKindChoices.MESH.value, scene=scene, mesh_collection=collection)

    await sync_to_async(place)()

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors

    path = result.data["scene"]["layers"][0]["pathToWorld"]
    assert path is not None, "a mesh layer is placed by the dataset its meshes were extracted from"
    assert path[-1]["transformation"]["output"]["residents"] == [], "the path ends in a space nothing lives in: a world"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_mesh_layer_has_no_path(authenticated_context: HttpContext):
    """The mirror image: nothing is registered, so there is nothing to inherit.

    Without this, a change that made every mesh layer resolve to *some* path would still
    look green against the test above.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Labels")
    collection = await _mesh_collection(authenticated_context, dataset)
    scene = await seed.create_scene(authenticated_context, "Composition")

    await sync_to_async(models.Layer.objects.create)(kind=enums.LayerKindChoices.MESH.value, scene=scene, mesh_collection=collection)

    result = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors

    assert result.data["scene"]["layers"][0]["pathToWorld"] is None
