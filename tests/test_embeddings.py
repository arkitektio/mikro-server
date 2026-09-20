"""Folders and datasets embed their name + description on save; stale rows heal in-process.

Real model (potion-base-8M), real Postgres with pgvector -- what the service runs. A re-embed
is not an edit: the provenance history must not grow and must not know the columns.
"""

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from kante.context import HttpContext

from core.models import ArrayDataset, Folder, TableDataset
from embeddings import engine
from embeddings.healer import reembed_all, reembed_stale
from tests.seed import _seed_parquet_store_sync, create_folder


async def create_array_dataset(ctx: HttpContext, name: str, description: str | None = None) -> ArrayDataset:
    """A bare array dataset (no coordinate graph -- these tests are about the text columns)."""
    return await ArrayDataset.objects.acreate(name=name, description=description, creator=ctx.request.user, organization=ctx.request.organization)


async def create_table_dataset(ctx: HttpContext, name: str, description: str | None = None) -> TableDataset:
    """A bare table dataset over a finished parquet store."""
    store = await sync_to_async(_seed_parquet_store_sync)(ctx, key=f"{name}.parquet")
    return await TableDataset.objects.acreate(name=name, description=description, store=store, creator=ctx.request.user, organization=ctx.request.organization)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_embeds_all_three_models(db, authenticated_context: HttpContext) -> None:
    """Each model gets a unit vector of the configured width, stamped with the model id."""
    ctx = authenticated_context
    rows = [
        await create_folder(ctx, "Screen 12", description="Control and treated wells of the kinase screen"),
        await create_array_dataset(ctx, "Plate 3 overview", "Widefield scan of the whole plate"),
        await create_table_dataset(ctx, "Localizations", "Single-molecule localizations with drift corrected"),
    ]
    for row in rows:
        await row.arefresh_from_db()
        assert row.embedding is not None, type(row).__name__
        assert len(row.embedding) == engine.dimensions() == 256
        assert abs(sum(x * x for x in row.embedding) - 1.0) < 1e-4
        assert row.embedding_model == engine.model_id()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_editing_the_description_reembeds(db, authenticated_context: HttpContext) -> None:
    """A changed description changes the vector; an unrelated ``update_fields`` save does not."""
    folder = await create_folder(authenticated_context, "Experiment", description="control wells")
    before = list((await Folder.objects.aget(pk=folder.pk)).embedding)

    folder = await Folder.objects.aget(pk=folder.pk)
    folder.description = "mitochondria in electron micrographs"
    await folder.asave()
    after = list((await Folder.objects.aget(pk=folder.pk)).embedding)
    assert after != before

    folder = await Folder.objects.aget(pk=folder.pk)
    folder.is_default = False
    await folder.asave(update_fields=["is_default"])
    assert list((await Folder.objects.aget(pk=folder.pk)).embedding) == after


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_blank_text_stores_null_and_is_complete(db, authenticated_context: HttpContext) -> None:
    """No text, no vector -- and the row is stamped so the healer never re-claims it."""
    dataset = await create_array_dataset(authenticated_context, "   ", None)
    await dataset.arefresh_from_db()

    assert dataset.embedding is None
    assert dataset.embedding_model == engine.model_id()
    assert await sync_to_async(reembed_stale)(ArrayDataset) == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_healer_reembeds_without_writing_history(db, authenticated_context: HttpContext) -> None:
    """Rows stamped by another model are re-embedded in place; no history row, no history column."""
    ctx = authenticated_context
    folder = await create_folder(ctx, "Blur", description="Gaussian blur of an image")
    dataset = await create_array_dataset(ctx, "Raw", "Unprocessed acquisition")
    table = await create_table_dataset(ctx, "Cells", "One row per segmented cell")
    for model, row in ((Folder, folder), (ArrayDataset, dataset), (TableDataset, table)):
        await model.objects.filter(pk=row.pk).aupdate(embedding=None, embedding_model="some/older-model")
    history_before = await folder.provenance.acount()

    assert await sync_to_async(reembed_all)([Folder, ArrayDataset, TableDataset]) == 3

    for model, row in ((Folder, folder), (ArrayDataset, dataset), (TableDataset, table)):
        fresh = await model.objects.aget(pk=row.pk)
        assert fresh.embedding is not None, model.__name__
        assert fresh.embedding_model == engine.model_id()
    assert await folder.provenance.acount() == history_before
    historical = folder.provenance.model
    assert not any(field.name in ("embedding", "embedding_model") for field in historical._meta.get_fields())
    assert await sync_to_async(reembed_all)([Folder, ArrayDataset, TableDataset]) == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disabled_writes_no_vector(db, authenticated_context: HttpContext) -> None:
    """With embeddings off, saving leaves both columns untouched and the healer is idle."""
    with override_settings(EMBEDDINGS={**engine._settings(), "ENABLED": False}):
        folder = await create_folder(authenticated_context, "Off", description="Nothing embeds")
        await folder.arefresh_from_db()
        assert folder.embedding is None
        assert folder.embedding_model == ""
        assert await sync_to_async(reembed_stale)(Folder) == 0
