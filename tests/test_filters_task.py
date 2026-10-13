"""Filters on the tasks query: created window, assigner, parent and search."""

import datetime

import pytest

from koherent.models import Task
from kante.context import HttpContext
from mikro_server.schema import schema

QUERY = """
    query Tasks($filters: TaskFilter) {
        tasks(filters: $filters) { taskId }
    }
"""


async def _seed_task(ctx: HttpContext, task_id: str, **overrides) -> Task:
    fields = dict(
        task_id=task_id,
        root_task_id=task_id,
        assigner=ctx.request.user,
        assigner_sub=ctx.request.user.sub,
        caller_sub=ctx.request.user.sub,
        agent_sub=ctx.request.user.sub,
        agent_client_id="mikroscope-app",
        issuer="rekuest",
        token_id=f"jti-{task_id}",
        args_hash="sha256-deadbeef",
        args_hash_algorithm="v1",
        organization=ctx.request.organization,
    )
    fields.update(overrides)
    return await Task.objects.acreate(**fields)


async def _query_task_ids(ctx: HttpContext, filters: dict) -> set[str]:
    result = await schema.execute(QUERY, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {t["taskId"] for t in result.data["tasks"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tasks_filter_by_created_window(db, authenticated_context: HttpContext):
    """createdBefore/createdAfter bound the task list by creation time."""
    early = await _seed_task(authenticated_context, "early")
    await _seed_task(authenticated_context, "late")
    # created_at is auto_now_add; move one row firmly into the past.
    cutoff = early.created_at - datetime.timedelta(days=1)
    await Task.objects.filter(pk=early.pk).aupdate(created_at=cutoff - datetime.timedelta(days=1))

    assert await _query_task_ids(authenticated_context, {"createdBefore": cutoff.isoformat()}) == {"early"}
    assert await _query_task_ids(authenticated_context, {"createdAfter": cutoff.isoformat()}) == {"late"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tasks_filter_by_assigner(db, authenticated_context: HttpContext):
    """assigner (sub) and assignerId (database ID) both narrow to the assigning user."""
    await _seed_task(authenticated_context, "mine")
    await _seed_task(authenticated_context, "nobodys", assigner=None, assigner_sub="ghost")

    user = authenticated_context.request.user
    assert await _query_task_ids(authenticated_context, {"assigner": user.sub}) == {"mine"}
    assert await _query_task_ids(authenticated_context, {"assignerId": str(user.id)}) == {"mine"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tasks_filter_by_parent_task_id(db, authenticated_context: HttpContext):
    """parentTaskId (FilterLookup) matches the parent task id string."""
    await _seed_task(authenticated_context, "child", parent_task_id="root-task")
    await _seed_task(authenticated_context, "orphan")

    assert await _query_task_ids(authenticated_context, {"parentTaskId": {"exact": "root-task"}}) == {"child"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tasks_search_matches_task_id_or_agent_client_id(db, authenticated_context: HttpContext):
    """search matches case-insensitively on either the task id or the executing agent client id."""
    await _seed_task(authenticated_context, "Deconvolution-Run", agent_client_id="other")
    await _seed_task(authenticated_context, "plain", agent_client_id="SegmentationSuite")

    assert await _query_task_ids(authenticated_context, {"search": "deconvolution"}) == {"Deconvolution-Run"}
    assert await _query_task_ids(authenticated_context, {"search": "segmentation"}) == {"plain"}
    assert await _query_task_ids(authenticated_context, {"search": "no-such-thing"}) == set()
