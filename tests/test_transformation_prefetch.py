"""Reading a pyramid's level edges must not trip Django's prefetch bookkeeping.

A level edge is a SEQUENCE (scale, then the half-voxel translation). Its children are read
through `SequenceTransformation.transformations`, and each child is asked for both its
`input` system and its `inputAxes`. Since `CoordinateSystem` became org-scoped, the optimizer
prefetches `input` with a *filtered* queryset, while the axis hints asked for a plain
`input__axes` -- and Django refuses one lookup seen with two querysets:

    'input' lookup was already seen with a different queryset.

That is the error `/mikro/arraydatasets/<id>` showed on every level above 0. This query is
the shape the dataset page sends; it must resolve cleanly and without an N+1.
"""

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests import seed

TRANSFORMATION = """
fragment Edge on Transformation {
  __typename
  id
  kind
  name
  version
  validity
  inputAxes
  outputAxes
  input { id name axes { name } }
  output { id name axes { name } }
  ... on ScaleTransformation { scale }
  ... on TranslationTransformation { translation }
  ... on AffineTransformation { affine }
  ... on FieldTransformation { field { id name } }
}
"""

QUERY = TRANSFORMATION + """
query Dataset($id: ID!) {
  arrayDataset(id: $id) {
    dataArrays {
      level
      toParent {
        ...Edge
        ... on SequenceTransformation { transformations { ...Edge } }
        ... on ByDimensionTransformation { transformations { ...Edge } }
      }
    }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_level_edges_resolve_with_their_children(authenticated_context: HttpContext):
    dataset = await seed.create_array_dataset(authenticated_context, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32], [3, 16, 16]])

    result = await schema.execute(QUERY, variable_values={"id": str(dataset.pk)}, context_value=authenticated_context)

    assert not result.errors, result.errors
    levels = sorted(result.data["arrayDataset"]["dataArrays"], key=lambda level: level["level"])
    assert levels[0]["toParent"] is None
    for level in levels[1:]:
        edge = level["toParent"]
        assert edge is not None
        assert edge["input"]["axes"] and edge["output"]["axes"]
        assert edge["__typename"] == "SequenceTransformation", "a level edge is scale-then-translate"
        assert [child["kind"] for child in edge["transformations"]] == ["SCALE", "TRANSLATION"]
        for child in edge["transformations"]:
            # A child omits its endpoints (the wrapper supplies them) but still states its axes.
            assert child["input"] is None
            assert child["inputAxes"] == [axis["name"] for axis in edge["input"]["axes"]]
