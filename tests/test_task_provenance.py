import pytest
from asgiref.sync import sync_to_async
from authentikate.provenance import ProvenanceToken
from koherent.models import Task
from kante.context import HttpContext
from mikro_server.schema import schema


def make_provenance(task_id="task-1", parent=None, args_hash="sha256-args", sub="1", cid="oinsoins") -> ProvenanceToken:
    """Build a verified provenance token, as AuthentikateExtension would attach one.

    koherent>=4 resolves the task from a signature-verified provenance token
    (set by AuthentikateExtension), not from a cleartext Rekuest-Task header, so
    tests inject the decoded token directly via ``attach_provenance``.
    """
    return ProvenanceToken(
        iss="rekuest",
        aud=["mikro"],
        sub=sub,
        act={"sub": sub, "cid": cid},
        iat=0,
        exp=4102444800,  # 2100-01-01, comfortably unexpired
        jti=f"jti-{task_id}",
        tsk=task_id,
        ptk=parent,
        rtk=task_id,
        rcb=sub,
        ahs=args_hash,
        aha="v1",
        raw="raw-provenance-token",
    )


def attach_provenance(ctx: HttpContext, provenance: ProvenanceToken) -> None:
    """Attach a provenance token to the request, as AuthentikateExtension would."""
    ctx.request.set_provenance(provenance)
    ctx.request.set_extension("provenance", provenance)


def clear_provenance(ctx: HttpContext) -> None:
    """Drop any attached provenance so the next operation runs unprovenanced.

    The context object is reused across executes in tests; a real request starts
    fresh, so detach the token the previous operation ran under.
    """
    ctx.request._provenance = None
    ctx.request._extensions.pop("provenance", None)


CREATE_DATASET = """
    mutation($name: String!) {
        createFolder(input: {name: $name}) {
            id
            createdThrough {
                taskId
                assignerSub
                agentClientId
                assigner {
                    sub
                }
            }
            createdThroughBy {
                sub
            }
        }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_with_provenance(db, authenticated_context: HttpContext):
    """A provenance token creates one Task row and stamps createdThrough."""
    attach_provenance(authenticated_context, make_provenance())

    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "Task created"},
        context_value=authenticated_context,
    )

    assert result.data, result.errors
    created_through = result.data["createFolder"]["createdThrough"]
    assert created_through is not None
    assert created_through["taskId"] == "task-1"
    assert created_through["assignerSub"] == "1"
    assert created_through["agentClientId"] == "oinsoins"
    assert created_through["assigner"]["sub"] == "1"
    assert result.data["createFolder"]["createdThroughBy"]["sub"] == "1"

    def check_row() -> None:
        task = Task.objects.get(task_id="task-1")
        assert task.args_hash == "sha256-args"
        assert task.organization == authenticated_context.request.organization
        assert task.assigner == authenticated_context.request.user

    await sync_to_async(check_row)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_without_provenance(db, authenticated_context: HttpContext):
    """Without a provenance token createdThrough stays None and no Task row is made."""
    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "Plain"},
        context_value=authenticated_context,
    )

    assert result.data, result.errors
    assert result.data["createFolder"]["createdThrough"] is None
    assert result.data["createFolder"]["createdThroughBy"] is None
    assert await sync_to_async(Task.objects.count)() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_task_row_shared_between_mutations(db, authenticated_context: HttpContext):
    """Two creations under the same task share a single Task row."""
    attach_provenance(authenticated_context, make_provenance(task_id="task-2"))

    for name in ("first", "second"):
        result = await schema.execute(
            CREATE_DATASET,
            variable_values={"name": name},
            context_value=authenticated_context,
        )
        assert result.data, result.errors

    assert await sync_to_async(Task.objects.count)() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_created_through_task(db, authenticated_context: HttpContext):
    """Folders are filterable by the task id they were created through."""
    attach_provenance(authenticated_context, make_provenance(task_id="task-3"))

    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "From task"},
        context_value=authenticated_context,
    )
    assert result.data, result.errors

    clear_provenance(authenticated_context)
    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "Not from task"},
        context_value=authenticated_context,
    )
    assert result.data, result.errors

    result = await schema.execute(
        """
        query($taskId: String!) {
            folders(filters: {createdThroughTask: $taskId}) {
                name
            }
        }
        """,
        variable_values={"taskId": "task-3"},
        context_value=authenticated_context,
    )

    assert result.data, result.errors
    assert [d["name"] for d in result.data["folders"]] == ["From task"]

    # assignedBy hits the denormalized created_through_by column.
    result = await schema.execute(
        """
        query($sub: ID!) {
            folders(filters: {assignedBy: $sub}) {
                name
            }
        }
        """,
        variable_values={"sub": "1"},
        context_value=authenticated_context,
    )

    assert result.data, result.errors
    assert [d["name"] for d in result.data["folders"]] == ["From task"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_filter_by_created_through_ids(db, authenticated_context: HttpContext):
    """Folders are filterable by the task's and the assigner's database IDs."""
    attach_provenance(authenticated_context, make_provenance(task_id="task-5"))

    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "From task"},
        context_value=authenticated_context,
    )
    assert result.data, result.errors

    clear_provenance(authenticated_context)
    result = await schema.execute(
        CREATE_DATASET,
        variable_values={"name": "Not from task"},
        context_value=authenticated_context,
    )
    assert result.data, result.errors

    task = await Task.objects.aget(task_id="task-5")

    # createdThrough matches the Task row's database ID (the `createdThrough { id }` field).
    result = await schema.execute(
        """
        query($task: ID!) {
            folders(filters: {createdThrough: $task}) {
                name
            }
        }
        """,
        variable_values={"task": str(task.id)},
        context_value=authenticated_context,
    )
    assert result.data, result.errors
    assert [d["name"] for d in result.data["folders"]] == ["From task"]

    # createdThroughBy matches the assigner's database user ID on the denormalized column.
    result = await schema.execute(
        """
        query($user: ID!) {
            folders(filters: {createdThroughBy: $user}) {
                name
            }
        }
        """,
        variable_values={"user": str(authenticated_context.request.user.id)},
        context_value=authenticated_context,
    )
    assert result.data, result.errors
    assert [d["name"] for d in result.data["folders"]] == ["From task"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_provenance_entry_links_task(db, authenticated_context: HttpContext):
    """The CREATE history entry links back to the task it happened under."""
    attach_provenance(authenticated_context, make_provenance(task_id="task-4"))

    result = await schema.execute(
        """
        mutation($name: String!) {
            createFolder(input: {name: $name}) {
                id
                provenanceEntries {
                    kind
                    task {
                        taskId
                    }
                }
            }
        }
        """,
        variable_values={"name": "Audited"},
        context_value=authenticated_context,
    )

    assert result.data, result.errors
    entries = result.data["createFolder"]["provenanceEntries"]
    assert entries, "Expected a CREATE provenance entry"
    assert entries[0]["task"] is not None
    assert entries[0]["task"]["taskId"] == "task-4"
