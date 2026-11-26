"""Files holding the same bytes: `File.duplicates`, `fileCandidates`, `fileDuplicateGroups`, `FileFilter.duplicated`."""

import pytest
from arkitekt_datalayer.models import BigFileStore
from kante.context import HttpContext

from core.models import File
from mikro_server.schema import schema
from tests.seed import create_file, create_folder

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


async def stored_file(
    ctx: HttpContext,
    name: str,
    *,
    digest: str | None,
    size: int = 1000,
    sample: str | None = "s" * 64,
) -> File:
    """A file on a finished store of ``size`` bytes with this digest and sample."""
    folder = await create_folder(ctx, f"folder of {name}")
    store = await BigFileStore.objects.acreate(
        organization=ctx.request.organization,
        key=f"key/{name}",
        bucket="bigfile",
        populated=True,
        size_bytes=size,
        digest=digest,
        sample=sample,
    )
    return await create_file(ctx, name, folder, store=store, size=size)


async def run(ctx: HttpContext, query: str, **variables: object) -> dict:
    result = await schema.execute(query, context_value=ctx, variable_values=variables)
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


DUPLICATES = "query ($id: ID!) { file(id: $id) { duplicates { name } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_names_the_files_with_its_digest_and_size(
    db: None, authenticated_context: HttpContext
) -> None:
    first = await stored_file(authenticated_context, "a.czi", digest=DIGEST_A)
    await stored_file(authenticated_context, "copy.czi", digest=DIGEST_A)
    await stored_file(authenticated_context, "other.czi", digest=DIGEST_B)
    await stored_file(authenticated_context, "owed.czi", digest=None)

    data = await run(authenticated_context, DUPLICATES, id=str(first.pk))

    assert data["file"]["duplicates"] == [{"name": "copy.czi"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_file_whose_digest_is_owed_has_no_duplicates(
    db: None, authenticated_context: HttpContext
) -> None:
    owed = await stored_file(authenticated_context, "owed.czi", digest=None)
    await stored_file(authenticated_context, "also-owed.czi", digest=None)

    data = await run(authenticated_context, DUPLICATES, id=str(owed.pk))

    assert data["file"]["duplicates"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_organizations_copy_is_never_named(
    db: None, authenticated_context: HttpContext, other_org_context: HttpContext
) -> None:
    mine = await stored_file(authenticated_context, "mine.czi", digest=DIGEST_A)
    await stored_file(other_org_context, "theirs.czi", digest=DIGEST_A)

    data = await run(authenticated_context, DUPLICATES, id=str(mine.pk))
    candidates = await run(
        authenticated_context,
        "query ($size: ByteCount!, $sample: String!) { fileCandidates(size: $size, sample: $sample) { name } }",
        size=1000,
        sample="s" * 64,
    )

    assert data["file"]["duplicates"] == []
    assert candidates["fileCandidates"] == [{"name": "mine.czi"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_candidates_match_size_and_sample(
    db: None, authenticated_context: HttpContext
) -> None:
    await stored_file(authenticated_context, "match.czi", digest=None, sample="x" * 64)
    await stored_file(
        authenticated_context, "bigger.czi", digest=None, size=2000, sample="x" * 64
    )
    await stored_file(
        authenticated_context, "different.czi", digest=None, sample="y" * 64
    )

    data = await run(
        authenticated_context,
        "query ($size: ByteCount!, $sample: String!) { fileCandidates(size: $size, sample: $sample) { name store { digest } } }",
        size=1000,
        sample="x" * 64,
    )

    assert data["fileCandidates"] == [{"name": "match.czi", "store": {"digest": None}}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_groups_list_the_most_redundant_bytes_first(
    db: None, authenticated_context: HttpContext
) -> None:
    for name in ("small-1", "small-2", "small-3"):
        await stored_file(authenticated_context, name, digest=DIGEST_A, size=10)
    for name in ("big-1", "big-2"):
        await stored_file(authenticated_context, name, digest=DIGEST_B, size=1000)
    await stored_file(authenticated_context, "alone", digest="c" * 64)

    data = await run(
        authenticated_context,
        "{ fileDuplicateGroups { digest size redundantBytes files { name } } }",
    )

    assert data["fileDuplicateGroups"] == [
        {
            "digest": DIGEST_B,
            "size": 1000,
            "redundantBytes": 1000,
            "files": [{"name": "big-1"}, {"name": "big-2"}],
        },
        {
            "digest": DIGEST_A,
            "size": 10,
            "redundantBytes": 20,
            "files": [{"name": "small-1"}, {"name": "small-2"}, {"name": "small-3"}],
        },
    ]
    page = await run(
        authenticated_context,
        "{ fileDuplicateGroups(pagination: {offset: 1, limit: 1}) { digest } }",
    )
    assert page["fileDuplicateGroups"] == [{"digest": DIGEST_A}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_duplicated_filter_splits_the_files(
    db: None, authenticated_context: HttpContext
) -> None:
    await stored_file(authenticated_context, "a", digest=DIGEST_A)
    await stored_file(authenticated_context, "b", digest=DIGEST_A)
    await stored_file(authenticated_context, "c", digest=DIGEST_B)
    await stored_file(authenticated_context, "owed", digest=None)
    query = "query ($d: Boolean!) { files(filters: {duplicated: $d}, ordering: [{name: ASC}]) { name } }"

    duplicated = await run(authenticated_context, query, d=True)
    alone = await run(authenticated_context, query, d=False)

    assert [f["name"] for f in duplicated["files"]] == ["a", "b"]
    assert [f["name"] for f in alone["files"]] == ["c", "owed"]
