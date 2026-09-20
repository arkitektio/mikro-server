"""The hybrid ``search`` on folders, array datasets and table datasets: lexical OR semantic, ranked.

Real model, real Postgres with pgvector, the real GraphQL pipeline (org prescope, filters,
ordering, pagination). Where a test needs rows at *known* distances from the query it writes
the vectors directly: ``e0`` is the real embedding of the query, and ``_vec(d)`` is a unit
vector at cosine distance ``d`` from it. That is real data in the real column, not a stub.

Folders search the name with full text, datasets with a substring; the semantic leg is the
same for all three, so the ranking cases run once per model through ``MODELS``.
"""

import math

import numpy as np
import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from kante.context import HttpContext

from core.models import ArrayDataset, Folder, TableDataset
from embeddings import engine
from mikro_server.schema import schema
from tests.seed import _seed_parquet_store_sync, create_folder

QUERY = "detect cells"


async def _folder(ctx: HttpContext, name: str, description: str | None = None) -> Folder:
    return await create_folder(ctx, name, description=description)


async def _array_dataset(ctx: HttpContext, name: str, description: str | None = None) -> ArrayDataset:
    return await ArrayDataset.objects.acreate(name=name, description=description, creator=ctx.request.user, organization=ctx.request.organization)


async def _table_dataset(ctx: HttpContext, name: str, description: str | None = None) -> TableDataset:
    store = await sync_to_async(_seed_parquet_store_sync)(ctx, key=f"{name}.parquet")
    return await TableDataset.objects.acreate(name=name, description=description, store=store, creator=ctx.request.user, organization=ctx.request.organization)


# (model, seed, GraphQL list field, its filter and order input names)
MODELS = [
    pytest.param(Folder, _folder, "folders", "FolderFilter", "FolderOrder", id="folder"),
    pytest.param(ArrayDataset, _array_dataset, "arrayDatasets", "ArrayDatasetFilter", "ArrayDatasetOrder", id="array_dataset"),
    pytest.param(TableDataset, _table_dataset, "tableDatasets", "TableDatasetFilter", "TableDatasetOrder", id="table_dataset"),
]


def _unit_orthogonal(e0: np.ndarray) -> np.ndarray:
    axis = np.zeros_like(e0)
    axis[int(np.argmin(np.abs(e0)))] = 1.0
    u = axis - float(np.dot(axis, e0)) * e0
    return u / np.linalg.norm(u)


def _vec(e0: np.ndarray, distance: float) -> list[float]:
    """A unit vector at cosine distance ``distance`` from ``e0``."""
    theta = math.acos(1.0 - distance)
    return (math.cos(theta) * e0 + math.sin(theta) * _unit_orthogonal(e0)).astype(float).tolist()


async def _pin(model, row, e0: np.ndarray, distance: float | None, embedding_model: str | None = None) -> None:
    """Overwrite a seeded row's vector with one at ``distance`` from ``e0`` (``None``: no vector)."""
    await model.objects.filter(pk=row.pk).aupdate(embedding=_vec(e0, distance) if distance is not None else None, embedding_model=embedding_model or engine.model_id())


async def _names(ctx: HttpContext, field: str, filter_name: str, order_name: str, search: str, ordering: list | None = None) -> list[str]:
    query = f"""
        query Search($filters: {filter_name}, $ordering: [{order_name}!]! = []) {{
            {field}(filters: $filters, ordering: $ordering) {{ name }}
        }}
    """
    result = await schema.execute(query, context_value=ctx, variable_values={"filters": {"search": search}, "ordering": ordering or []})
    assert not result.errors, result.errors
    return [row["name"] for row in result.data[field]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "seed", "field", "filter_name", "order_name"), MODELS)
async def test_semantic_match_without_lexical_overlap(db, authenticated_context: HttpContext, model, seed, field, filter_name, order_name) -> None:
    """A description that means the query is found although no word of the query is in the name."""
    ctx = authenticated_context
    await seed(ctx, "Segmented nuclei", "Find cell nuclei in a fluorescence image and detect every cell")
    await seed(ctx, "Spreadsheet export", "A table written to an xlsx file on disk")

    names = await _names(ctx, field, filter_name, order_name, QUERY)

    assert "Segmented nuclei" in names
    assert "Spreadsheet export" not in names


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "seed", "field", "filter_name", "order_name"), MODELS)
async def test_lexical_only_when_disabled(db, authenticated_context: HttpContext, model, seed, field, filter_name, order_name) -> None:
    """With embeddings off the filter is exactly the old lexical match."""
    ctx = authenticated_context
    await seed(ctx, "Detect cells", None)
    await seed(ctx, "Segmented nuclei", "detect cells in an image")

    with override_settings(EMBEDDINGS={**engine._settings(), "ENABLED": False}):
        names = await _names(ctx, field, filter_name, order_name, QUERY)

    assert names == ["Detect cells"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "seed", "field", "filter_name", "order_name"), MODELS)
async def test_ranking_lexical_first_then_by_distance(db, authenticated_context: HttpContext, model, seed, field, filter_name, order_name) -> None:
    """Lexical hits first (even without a vector), then semantic hits nearest first, far ones cut."""
    ctx = authenticated_context
    e0 = np.asarray(engine.embed_query(QUERY))
    await _pin(model, await seed(ctx, "Far", None), e0, 0.5)
    await _pin(model, await seed(ctx, "Near", None), e0, 0.1)
    await _pin(model, await seed(ctx, "Mid", None), e0, 0.3)
    await _pin(model, await seed(ctx, "Beyond", None), e0, 0.7)
    await _pin(model, await seed(ctx, "Detect cells here", None), e0, None)

    assert await _names(ctx, field, filter_name, order_name, QUERY) == ["Detect cells here", "Near", "Mid", "Far"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "seed", "field", "filter_name", "order_name"), MODELS)
async def test_stale_embedding_model_is_not_a_vector_hit(db, authenticated_context: HttpContext, model, seed, field, filter_name, order_name) -> None:
    """A row embedded by another model is skipped by the vector leg, still found lexically."""
    ctx = authenticated_context
    e0 = np.asarray(engine.embed_query(QUERY))
    await _pin(model, await seed(ctx, "Old model near", None), e0, 0.05, "some/older-model")
    await _pin(model, await seed(ctx, "Old model detect cells", None), e0, 0.05, "some/older-model")

    assert await _names(ctx, field, filter_name, order_name, QUERY) == ["Old model detect cells"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "seed", "field", "filter_name", "order_name"), MODELS)
async def test_explicit_ordering_replaces_the_ranking(db, authenticated_context: HttpContext, model, seed, field, filter_name, order_name) -> None:
    """A client's ``ordering`` wins over the distance ranking."""
    ctx = authenticated_context
    e0 = np.asarray(engine.embed_query(QUERY))
    await _pin(model, await seed(ctx, "Zeta", None), e0, 0.1)
    await _pin(model, await seed(ctx, "Alpha", None), e0, 0.4)

    assert await _names(ctx, field, filter_name, order_name, QUERY) == ["Zeta", "Alpha"]
    assert await _names(ctx, field, filter_name, order_name, QUERY, ordering=[{"name": "ASC"}]) == ["Alpha", "Zeta"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unloadable_model_degrades_to_lexical(db, authenticated_context: HttpContext) -> None:
    """When the weights cannot be loaded the query still answers, lexical-only."""
    ctx = authenticated_context
    e0 = np.asarray(engine.embed_query(QUERY))
    await _pin(Folder, await _folder(ctx, "Near"), e0, 0.05)
    await _folder(ctx, "Detect cells")

    try:
        with override_settings(EMBEDDINGS={**engine._settings(), "MODEL_PATH": "/nonexistent/embeddings"}):
            engine.reset()
            assert await _names(ctx, "folders", "FolderFilter", "FolderOrder", QUERY) == ["Detect cells"]
    finally:
        engine.reset()


def test_nested_search_stays_lexical() -> None:
    """Used through a relation (a non-empty ``prefix``) the helper adds nothing: the lexical ``Q`` as given."""
    from django.db.models import Q

    from embeddings.search import hybrid_search

    lexical = Q(folder__name__search="detect")
    queryset, predicate = hybrid_search(Folder.objects.all(), "folder__", "detect", lexical)

    assert predicate == lexical
    assert not queryset.query.annotations
    assert not queryset.query.order_by
