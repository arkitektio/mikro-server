"""The home page's counts: one aggregate per list the UI has a page for.

They pin that a count is the length of its list -- scoped to the organization, so another
tenant's rows never show up as a number -- and that the list's own filter narrows it, which
is how the home page asks for "new this week".
"""

import datetime

import pytest
from kante.context import HttpContext

from mikro_server.schema import schema
from tests import seed

COUNTS = """
query Counts($since: DateTime!) {
  foldersStats { count }
  newFolders: foldersStats(filters: { createdAfter: $since }) { count }
  filesStats { count }
  scenesStats { count }
  lensesStats { count }
  coordinateSystemsStats { count }
}
"""


async def _counts(ctx: HttpContext, since: datetime.datetime) -> dict:
    result = await schema.execute(COUNTS, context_value=ctx, variable_values={"since": since.isoformat()})
    assert not result.errors, result.errors
    return {key: value["count"] for key, value in result.data.items()}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_count_is_what_the_organization_holds(authenticated_context: HttpContext, other_org_context: HttpContext):
    folder = await seed.create_folder(authenticated_context, "Raw")
    await seed.create_folder(authenticated_context, "Processed")
    await seed.create_file(authenticated_context, "scan.lif", folder)
    await seed.create_scene(authenticated_context, "Overview")
    await seed.create_array_dataset(authenticated_context, "Image")

    week_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    mine = await _counts(authenticated_context, week_ago)
    listed = await schema.execute("{ folders { id } scenes { id } }", context_value=authenticated_context)
    assert not listed.errors
    assert mine["foldersStats"] == len(listed.data["folders"])
    assert mine["scenesStats"] == len(listed.data["scenes"]) >= 1
    assert mine["filesStats"] == 1
    assert mine["lensesStats"] >= 1, "a dataset brings its whole lens"
    assert mine["coordinateSystemsStats"] >= 1, "and its intrinsic coordinate system"

    assert mine["newFolders"] == mine["foldersStats"], "everything here was made this week"
    later = await _counts(authenticated_context, datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1))
    assert later["newFolders"] == 0, "the list's filter narrows the count"

    theirs = await _counts(other_org_context, week_ago)
    assert theirs == {key: 0 for key in theirs}, "another tenant's rows are not counted"
