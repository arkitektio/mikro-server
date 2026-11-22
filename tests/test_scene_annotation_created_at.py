"""Scenes and annotations say when they were made, and the rows from before the column get their history's time.

Migration 0014 backfills from the first history row and never stamps the migration's own time:
a "new this week" over a hub that just migrated would otherwise list everything.
"""

import datetime
import importlib

import pytest
from asgiref.sync import sync_to_async
from django.apps import apps as global_apps
from django.utils import timezone
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed

backfill_migration = importlib.import_module("core.migrations.0014_backfill_scene_annotation_created_at")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_scene_is_filtered_and_ordered_by_when_it_was_made(authenticated_context: HttpContext):
    ctx = authenticated_context
    before = timezone.now() - datetime.timedelta(seconds=1)
    await seed.create_scene(ctx, "Old")
    await seed.create_scene(ctx, "New")

    def age_old() -> None:
        models.Scene.objects.filter(name="Old").update(created_at=timezone.now() - datetime.timedelta(days=30))

    await sync_to_async(age_old)()
    result = await schema.execute(
        """
        query Q($after: DateTime!) {
          recent: scenes(filters: { createdAfter: $after }) { name createdAt }
          ordered: scenes(ordering: [{ createdAt: DESC }]) { name }
          stats: scenesStats(filters: { createdAfter: $after }) { count }
        }
        """,
        context_value=ctx,
        variable_values={"after": before.isoformat()},
    )
    assert not result.errors, result.errors
    assert result.data
    assert [row["name"] for row in result.data["recent"]] == ["New"]
    assert result.data["recent"][0]["createdAt"]
    assert [row["name"] for row in result.data["ordered"]] == ["New", "Old"]
    assert result.data["stats"]["count"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_backfill_takes_the_first_history_row_and_leaves_none_null(authenticated_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Before the column")
    drawn_at = timezone.now() - datetime.timedelta(days=400)

    def forget_and_backfill() -> tuple[datetime.datetime | None, list[datetime.datetime | None]]:
        models.Scene.objects.filter(pk=scene.pk).update(created_at=None)
        global_apps.get_model("core", "HistoricalScene").objects.filter(id=scene.pk).update(history_date=drawn_at)
        orphan = models.Scene.objects.create(name="No history", organization=ctx.request.organization, world=scene.world)
        models.Scene.objects.filter(pk=orphan.pk).update(created_at=None)
        global_apps.get_model("core", "HistoricalScene").objects.filter(id=orphan.pk).delete()
        backfill_migration.backfill(global_apps, None)
        return models.Scene.objects.get(pk=scene.pk).created_at, list(models.Scene.objects.filter(pk=orphan.pk).values_list("created_at", flat=True))

    restored, orphaned = await sync_to_async(forget_and_backfill)()
    assert restored == drawn_at
    assert orphaned == [None], "a row with no history stays null rather than taking the migration's time"
