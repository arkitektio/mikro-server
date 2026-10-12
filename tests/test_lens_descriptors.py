"""Every container and every lens kind describes itself, and a lens describes what it selects.

One declaration per kind (``mikro_server.vocabulary``), one describer per kind
(``core.descriptors``), bound once (``mikro_server.service``) to feed the manifest, the signals
and the ``descriptors`` field. These hold the three to each other across the six kinds: a
container and its whole lens answer alike, a lens that selects something answers for the
selection, and nothing is declared that is not computed.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from mikro_server.service import service
from mikro_server.vocabulary import DESCRIPTORS_BY_KIND, IS_WHOLE, VALUE_KIND
from tests import seed
from tests.test_derived_datasets import _derive
from tests.test_lens_geometry import _collection_on, _draw
from tests.test_windowed_lenses import _CONTAINERS, _create

CONTAINER_FIELD = {"TABLE": "tableDataset", "SPARSE": "sparseDataset", "MESH": "meshCollection", "NETWORK": "networkCollection", "ANNOTATION": "annotationCollection"}

DESCRIBED = """
query Described($container: ID!, $lens: ID!) {
  tableDataset(id: $container) { descriptors } sparseDataset(id: $container) { descriptors }
  meshCollection(id: $container) { descriptors } networkCollection(id: $container) { descriptors }
  annotationCollection(id: $container) { descriptors }
  lens(id: $lens) { descriptors }
}
"""


async def _described(ctx: HttpContext, field: str, container_id: str, lens_id: str) -> tuple[dict, dict]:
    """The container's descriptors through its own field, and a lens' through the interface."""
    query = DESCRIBED.replace(
        "tableDataset(id: $container) { descriptors } sparseDataset(id: $container) { descriptors }\n  meshCollection(id: $container) { descriptors } networkCollection(id: $container) { descriptors }\n  annotationCollection(id: $container) { descriptors }",
        f"{field}(id: $container) {{ descriptors }}",
    )
    result = await schema.execute(query, context_value=ctx, variable_values={"container": container_id, "lens": lens_id})
    assert not result.errors, result.errors
    return result.data[field]["descriptors"], result.data["lens"]["descriptors"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_a_container_and_its_whole_lens_describe_alike_and_a_window_says_it_is_not_whole(authenticated_context: HttpContext, kind: str):
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)
    whole = await model.objects.aget(**{model.CONTAINER_FIELD + "_id": container_id})
    windowed = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])

    container, lens = await _described(ctx, CONTAINER_FIELD[kind], container_id, str(whole.pk))
    assert container == lens, "the whole lens is the container, looked at whole"
    assert container[IS_WHOLE] is True
    declared = {d.key for d in DESCRIPTORS_BY_KIND[kind]}
    assert set(container) == declared, "every declared key is computed and every computed key declared"

    _, window = await _described(ctx, CONTAINER_FIELD[kind], container_id, windowed["id"])
    assert window[IS_WHOLE] is False
    assert {key: value for key, value in window.items() if key not in (IS_WHOLE, "@mikro/n_annotations")} == {key: value for key, value in lens.items() if key not in (IS_WHOLE, "@mikro/n_annotations")}, "a window changes nothing the schema says"

    row = await models.Lens.objects.with_sources().aget(pk=whole.pk)
    assert await sync_to_async(service.describe)(row) == lens, "the field answers what a signal about the lens carries"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_table_says_what_its_schema_says(authenticated_context: HttpContext):
    """Where its rows sit and what they carry -- never how many there are."""
    ctx = authenticated_context
    make, model, _ = _CONTAINERS["TABLE"]
    table_id, _, _ = await make(ctx)
    whole = await model.objects.aget(table_dataset_id=table_id)
    table, _ = await _described(ctx, "tableDataset", table_id, str(whole.pk))
    assert table == {
        "@mikro/n_space_axes": 1,
        "@mikro/n_time_axes": 1,
        "@mikro/n_index_axes": 0,
        "@mikro/n_coordinate_columns": 2,
        "@mikro/n_attribute_columns": 1,
        "@mikro/has_track_ids": False,
        "@mikro/has_group_ids": False,
        "@mikro/has_labels": False,
        "@mikro/spec": ["POINTS_1D", "TIMED"],
        IS_WHOLE: True,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_matrix_says_its_rank_and_its_layouts(authenticated_context: HttpContext):
    ctx = authenticated_context
    make, model, _ = _CONTAINERS["SPARSE"]
    sparse_id, _, _ = await make(ctx)
    whole = await model.objects.aget(sparse_dataset_id=sparse_id)
    sparse, _ = await _described(ctx, "sparseDataset", sparse_id, str(whole.pk))
    assert sparse["@mikro/n_axes"] == 2 and sparse["@mikro/n_index_axes"] == 2, "an object-by-feature matrix: two enumerating axes"
    assert sparse["@mikro/n_layouts"] == 2, "the store holds a layout per axis"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_annotation_lens_counts_the_shapes_in_its_windows(authenticated_context: HttpContext):
    """The whole collection counts every shape; a window counts the ones drawn inside it, as the overlap filter judges them."""
    ctx = authenticated_context
    make, model, _ = _CONTAINERS["ANNOTATION"]
    collection_id, mutation, field = await make(ctx)
    await _draw(ctx, collection_id, "POINT", [[2.0, 3.0]])
    await _draw(ctx, collection_id, "POINT", [[20.0, 3.0]])
    whole = await model.objects.aget(annotation_collection_id=collection_id)
    windowed = await _create(ctx, mutation, field, collection_id, [{"axis": "y", "min": 0, "max": 5}])

    collection, lens = await _described(ctx, "annotationCollection", collection_id, str(whole.pk))
    assert collection["@mikro/n_annotations"] == lens["@mikro/n_annotations"] == 2
    _, window = await _described(ctx, "annotationCollection", collection_id, windowed["id"])
    assert window["@mikro/n_annotations"] == 1, "the point on row 20 is outside y 0..5"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_arrays_value_kind_is_read_off_what_its_author_declared(authenticated_context: HttpContext):
    """A derivation declared CATEGORIZED makes a label map; a DISPLACEMENT value axis a vector field; the rest is intensity -- on the dataset, its whole lens and a crop alike."""
    ctx = authenticated_context
    source = await seed.create_array_dataset(ctx, "raw", axes=seed.YX_AXES, shapes=[[64, 64]])
    source_lens = await seed.create_lens(ctx, source)
    derived = await _derive(ctx, "mask", axes=seed.YX_AXES, shape=[64, 64], lens=source_lens, valueRelation="CATEGORIZED")
    assert not derived.errors, derived.errors
    mask = await models.ArrayDataset.objects.aget(pk=derived.data["createArrayDataset"]["id"])
    mask_crop = await seed.create_lens(ctx, mask, slices=[{"axis": "y", "start": 0, "stop": 8}])
    flow = await seed.create_array_dataset(ctx, "flow", axes=[seed.axis("v", enums.AxisType.DISPLACEMENT), *seed.YX_AXES], shapes=[[2, 64, 64]])

    async def kind_of(dataset: models.ArrayDataset, lens: models.Lens | None = None) -> tuple[str, str]:
        lens = lens or await models.ArrayLens.objects.aget(dataset=dataset, slices=[])
        result = await schema.execute("query K($d: ID!, $l: ID!) { arrayDataset(id: $d) { descriptors } lens(id: $l) { descriptors } }", context_value=ctx, variable_values={"d": str(dataset.pk), "l": str(lens.pk)})
        assert not result.errors, result.errors
        return result.data["arrayDataset"]["descriptors"][VALUE_KIND], result.data["lens"]["descriptors"][VALUE_KIND]

    assert await kind_of(source) == ("intensity", "intensity")
    assert await kind_of(mask) == ("categorical", "categorical")
    assert (await kind_of(mask, mask_crop))[1] == "categorical", "a crop of a label map is still labels"
    assert await kind_of(flow) == ("vector", "vector")
