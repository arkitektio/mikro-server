"""`shareWorkflow`: what a workflow (a root task) created in mikro goes in a collaboration, as it is now.

Everything here is made through the real mutations, under provenance tokens whose `rtk` names the
workflow: two steps of `workflow-7`, and a step of another workflow. A whole the workflow made goes
in; a part comes along with it; a shape the workflow drew into a collection made *before* it is
left out and reported, until that collection is shared. A guest granted the key sees exactly what
went in, and a member's lists narrow to it (`inCollaboration`) or to the workflow (`createdUnder`).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from arkitekt_service.collaborations.models import Collaboration
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_cross_container_derivation import _MEASUREMENT_COLUMNS, _table
from tests.test_task_provenance import (
    attach_provenance,
    clear_provenance,
    make_provenance,
)

WORKFLOW = "workflow-7"

SHARE = """
mutation ($input: ShareWorkflowInput!) {
  shareWorkflow(input: $input) {
    collaboration dryRun covered unknown
    added { identifier object }
    skipped { identifier object wholeIdentifier whole }
  }
}
"""
DRAW = """
mutation ($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id collection { id } }
}
"""
FOLDER = "mutation ($name: String!) { createFolder(input: {name: $name}) { id } }"
CHART = "mutation ($input: CreateChartInput!) { createChart(input: $input) { id } }"
ADD = """
mutation ($input: CollaborationObjectInput!) {
  addToCollaboration(input: $input) { key }
}
"""

#: What mikro hosts but cannot say what created: an anchor (pinned by whoever, whenever, into a
#: container) and the stores (brought along by what they hold, never made on their own).
UNKNOWN = {
    "@mikro/coordinateanchor",
    "@mikro/bigfilestore",
    "@mikro/mediastore",
    "@mikro/zarrstore",
    "@mikro/parquetstore",
    "@mikro/fabriksstore",
    "@mikro/kulissestore",
    "@mikro/konnektionstore",
    "@mikro/sparsestore",
}


async def _run(ctx: HttpContext, query: str, **variables: object) -> ExecutionResult:
    return await schema.execute(
        query, context_value=ctx, variable_values=variables or None
    )


async def _ok(ctx: HttpContext, query: str, **variables: object) -> dict:
    result = await _run(ctx, query, **variables)
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


async def _draw(ctx: HttpContext, **target: str) -> dict:
    """A point drawn into a collection, or onto a scene (into the collection minted for it)."""
    return (
        await _ok(
            ctx,
            DRAW,
            input={**target, "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]},
        )
    )["createAnnotation"]


@dataclass(frozen=True)
class Made:
    """The ids of what was made: before the workflow, in it, and in another workflow."""

    old_table: str
    old_collection: str
    table: str
    folder: str
    chart: str
    collection: str
    shape: str  # drawn in the workflow into the collection it minted
    late_shape: str  # drawn in the workflow into the collection made before it
    elsewhere: str  # a folder of another workflow


async def _made(ctx: HttpContext) -> Made:
    canvas = await seed.create_scene(ctx, "Before")
    fresh = await seed.create_scene(ctx, "During")

    clear_provenance(ctx)
    old_table = await _table(ctx, "Before", columns=_MEASUREMENT_COLUMNS)
    old = await _draw(ctx, scene=str(canvas.pk))

    attach_provenance(ctx, make_provenance(task_id="step-1", root=WORKFLOW))
    table = await _table(ctx, "Made", columns=_MEASUREMENT_COLUMNS)
    folder = (await _ok(ctx, FOLDER, name="Made"))["createFolder"]
    chart = (
        await _ok(
            ctx,
            CHART,
            input={
                "name": "Profile",
                "axis": {"name": "t", "type": "TIME", "unit": "second"},
            },
        )
    )["createChart"]

    attach_provenance(ctx, make_provenance(task_id="step-2", root=WORKFLOW))
    shape = await _draw(ctx, scene=str(fresh.pk))
    late = await _draw(ctx, collection=old["collection"]["id"])

    attach_provenance(ctx, make_provenance(task_id="other-1", root="workflow-8"))
    elsewhere = (await _ok(ctx, FOLDER, name="Elsewhere"))["createFolder"]
    clear_provenance(ctx)

    return Made(
        old_table=old_table["id"],
        old_collection=old["collection"]["id"],
        table=table["id"],
        folder=folder["id"],
        chart=chart["id"],
        collection=shape["collection"]["id"],
        shape=shape["id"],
        late_shape=late["id"],
        elsewhere=elsewhere["id"],
    )


async def _share(ctx: HttpContext, key: str, *, dry_run: bool) -> dict:
    return (
        await _ok(
            ctx,
            SHARE,
            input={
                "collaboration": key,
                "rootTask": WORKFLOW,
                "dryRun": dry_run,
                "name": "Workflow 7",
            },
        )
    )["shareWorkflow"]


def _added(report: dict) -> set[tuple[str, str]]:
    return {(a["identifier"], a["object"]) for a in report["added"]}


def _skipped(report: dict) -> set[tuple[str, str, str, str]]:
    return {
        (s["identifier"], s["object"], s["wholeIdentifier"], s["whole"])
        for s in report["skipped"]
    }


async def _ids(
    ctx: HttpContext, query: str, field: str, **variables: object
) -> set[str]:
    return {row["id"] for row in (await _ok(ctx, query, **variables))[field]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dry_run_says_what_sharing_puts_in_and_writes_nothing(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    made = await _made(ctx)
    key = str(uuid.uuid4())

    dry = await _share(ctx, key, dry_run=True)
    assert dry["dryRun"] is True
    assert dry["collaboration"] == key
    assert not await sync_to_async(Collaboration.objects.filter(key=key).exists)()

    done = await _share(ctx, key, dry_run=False)
    assert done["dryRun"] is False
    assert _added(dry) == _added(done)
    assert _skipped(dry) == _skipped(done)
    assert dry["covered"] == done["covered"]

    added = _added(done)
    assert {
        ("@mikro/tabledataset", made.table),
        ("@mikro/folder", made.folder),
        ("@mikro/chart", made.chart),
        ("@mikro/annotationcollection", made.collection),
    } <= added
    # What the workflow did not make, and parts (covered by their whole), are not put in by hand.
    assert (
        not {
            ("@mikro/tabledataset", made.old_table),
            ("@mikro/folder", made.elsewhere),
            ("@mikro/annotation", made.shape),
            ("@mikro/annotation", made.late_shape),
            ("@mikro/annotationcollection", made.old_collection),
        }
        & added
    )
    assert done["covered"] >= 1  # the shape, with its collection
    assert _skipped(done) == {
        (
            "@mikro/annotation",
            made.late_shape,
            "@mikro/annotationcollection",
            made.old_collection,
        )
    }
    assert set(done["unknown"]) == UNKNOWN
    assert (
        await sync_to_async(Collaboration.objects.get)(key=key)
    ).name == "Workflow 7"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_granted_the_key_sees_exactly_what_was_shared(
    authenticated_context: HttpContext, guest: Callable[[dict], HttpContext]
) -> None:
    made = await _made(authenticated_context)
    key = str(uuid.uuid4())
    await _share(authenticated_context, key, dry_run=False)
    ctx = guest({key: []})

    assert await _ids(ctx, "{ tableDatasets { id } }", "tableDatasets") == {made.table}
    assert await _ids(ctx, "{ folders { id } }", "folders") == {made.folder}
    assert await _ids(ctx, "{ charts { id } }", "charts") == {made.chart}
    assert await _ids(
        ctx, "{ annotationCollections { id } }", "annotationCollections"
    ) == {made.collection}
    assert await _ids(ctx, "{ annotations { id } }", "annotations") == {made.shape}

    # Another key, granted, holds nothing here.
    elsewhere = guest({str(uuid.uuid4()): []})
    assert await _ids(elsewhere, "{ annotations { id } }", "annotations") == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lists_narrow_to_a_collaboration_and_to_a_workflow(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    made = await _made(ctx)
    key = str(uuid.uuid4())
    await _share(ctx, key, dry_run=False)

    in_it = "query ($v: ID!) { %s(filters: {inCollaboration: $v}) { id } }"
    under = "query ($v: ID!) { %s(filters: {createdUnder: $v}) { id } }"

    assert await _ids(ctx, in_it % "tableDatasets", "tableDatasets", v=key) == {
        made.table
    }
    # A shape is in with its collection: put in nowhere by hand, but part of what was.
    assert await _ids(ctx, in_it % "annotations", "annotations", v=key) == {made.shape}
    assert await _ids(ctx, in_it % "folders", "folders", v=str(uuid.uuid4())) == set()

    assert await _ids(ctx, under % "tableDatasets", "tableDatasets", v=WORKFLOW) == {
        made.table
    }
    assert await _ids(ctx, under % "folders", "folders", v=WORKFLOW) == {made.folder}
    assert await _ids(ctx, under % "charts", "charts", v=WORKFLOW) == {made.chart}
    # Both shapes are the workflow's, whether or not they went in.
    assert await _ids(ctx, under % "annotations", "annotations", v=WORKFLOW) == {
        made.shape,
        made.late_shape,
    }
    assert await _ids(ctx, under % "folders", "folders", v="workflow-8") == {
        made.elsewhere
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_part_of_an_older_whole_goes_in_once_that_whole_is_shared(
    authenticated_context: HttpContext,
) -> None:
    ctx = authenticated_context
    made = await _made(ctx)
    key = str(uuid.uuid4())
    first = await _share(ctx, key, dry_run=False)
    assert first["skipped"]

    await _ok(
        ctx,
        ADD,
        input={
            "collaboration": key,
            "identifier": "@mikro/annotationcollection",
            "object": made.old_collection,
        },
    )
    again = await _share(ctx, key, dry_run=False)

    assert again["skipped"] == []
    shapes = await _ids(
        ctx,
        "query ($v: ID!) { annotations(filters: {inCollaboration: $v}) { id } }",
        "annotations",
        v=key,
    )
    assert {made.shape, made.late_shape} <= shapes
    assert (
        await sync_to_async(
            models.Annotation.objects.filter(collection_id=made.old_collection).count
        )()
        == 2
    )  # the older shape comes with its collection too
