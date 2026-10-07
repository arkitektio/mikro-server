"""Folders and datasets embed their name + description on save, with the release's model.

Real model (potion-base-8M), real Postgres with pgvector -- what the service runs. An
embedding is not an edit: the provenance history must not know the column.
"""

import pytest
from asgiref.sync import sync_to_async
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from kante.context import HttpContext

from core.models import ArrayDataset, Folder, TableDataset
from embeddings import engine
from mikro_server.schema import schema
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
    """Each model gets a unit vector of the release's width."""
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
async def test_blank_text_stores_null(db, authenticated_context: HttpContext) -> None:
    """No text, no vector (never a zero vector)."""
    dataset = await create_array_dataset(authenticated_context, "   ", None)
    await dataset.arefresh_from_db()

    assert dataset.embedding is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_row_saved_without_the_model_has_no_vector_until_it_is_saved_again(db, authenticated_context: HttpContext, monkeypatch) -> None:
    """The write must not fail because the model did; the row's next save embeds it."""

    def unavailable() -> None:
        raise engine.EmbeddingsUnavailable("no weights")

    with monkeypatch.context() as patched:
        patched.setattr(engine, "_model", unavailable)
        folder = await create_folder(authenticated_context, "Blur", description="Gaussian blur of an image")
    assert (await Folder.objects.aget(pk=folder.pk)).embedding is None

    folder = await Folder.objects.aget(pk=folder.pk)
    await folder.asave()
    assert (await Folder.objects.aget(pk=folder.pk)).embedding is not None


def test_history_does_not_know_the_column() -> None:
    """The vector is storage, not an edit: no historical model carries it."""
    for model in (Folder, ArrayDataset, TableDataset):
        assert not any(field.name == "embedding" for field in model.provenance.model._meta.get_fields()), model.__name__


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_disabled_writes_no_vector(db, authenticated_context: HttpContext) -> None:
    """With embeddings off, saving leaves the column untouched."""
    with override_settings(EMBEDDINGS={**engine._settings(), "ENABLED": False}):
        folder = await create_folder(authenticated_context, "Off", description="Nothing embeds")
        await folder.arefresh_from_db()
        assert folder.embedding is None


@pytest.mark.django_db
def test_no_system_check_loads_the_model(monkeypatch) -> None:
    """``manage.py migrate`` runs the checks: the model is the server's to load, not a command's."""

    def loaded() -> None:
        raise AssertionError("a system check loaded the embedding model")

    monkeypatch.setattr(engine, "_model", loaded)
    assert not [message for message in checks.run_checks(databases=["default"]) if message.id.startswith("embeddings.")]


def test_weights_baked_for_another_model_are_refused(tmp_path, monkeypatch) -> None:
    """The image's ``MODEL_ID`` stamp must name the release's model: anything else is a broken build."""
    (tmp_path / engine.MODEL_ID_FILENAME).write_text("some/other-model")
    monkeypatch.setattr(engine, "MODEL_PATH", str(tmp_path))
    engine.reset()
    try:
        with pytest.raises(ImproperlyConfigured, match="some/other-model"):
            engine.warm_up()
    finally:
        monkeypatch.undo()
        engine.reset()


EMBEDDING_QUERY = "query($id: ID!){ folder(id: $id){ name embedding } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_stored_vector_is_published_with_its_model_id(authenticated_context: HttpContext) -> None:
    """A vector without the model that produced it is not comparable to anything, so the
    descriptor travels inside the value rather than beside it."""
    from embeddings.strawberry import format_embedding

    folder = await create_folder(authenticated_context, name="Segmentations")
    await folder.arefresh_from_db()

    result = await schema.execute(EMBEDDING_QUERY, context_value=authenticated_context, variable_values={"id": str(folder.pk)})
    assert not result.errors, result.errors
    published = result.data["folder"]["embedding"]

    model_id, _, floats = published.partition(":")
    assert model_id == engine.model_id()
    # The floats round-trip exactly, so a client can reuse the vector it was handed.
    assert [float(component) for component in floats.split(",")] == folder.embedding
    assert published == format_embedding(folder.embedding, engine.model_id())


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_row_without_a_vector_publishes_null(authenticated_context: HttpContext) -> None:
    folder = await create_folder(authenticated_context, name="Unembedded")
    await Folder.objects.filter(pk=folder.pk).aupdate(embedding=None)

    result = await schema.execute(EMBEDDING_QUERY, context_value=authenticated_context, variable_values={"id": str(folder.pk)})
    assert not result.errors, result.errors

    assert result.data["folder"]["embedding"] is None
