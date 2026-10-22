"""A page of tiles costs the same however many tiles are on it.

The home page lists files with the array datasets converted out of them, and every list
page lists datasets as tiles. Each tile field (`shape`, `axisNames`, `multiscale`,
`fullLens`, `defaultScene`, `latestSnapshot`) used to cost a query per dataset, and reached
through `File.derivedContainers` none of their prefetches applied.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db.backends.utils import CursorWrapper
from kante.context import HttpContext

from core import enums, models
from mikro_server.schema import schema
from tests import seed

TILE = "id name axisNames shape multiscale spec fullLens { id } defaultScene { id } latestSnapshot { id }"
FILES = "query { files(pagination: {limit: 20}) { id derivedContainers(filters: {direction: SOURCE}) { id seriesIdentifier container { __typename ... on ArrayDataset { %s } } } } }" % TILE
DATASETS = "query { arrayDatasets(pagination: {limit: 50}) { %s } }" % TILE


async def _files_with_series(ctx: HttpContext, files: int, series: int, offset: int) -> None:
    folder = await seed.create_folder(ctx, f"Tiles{offset}")
    for f in range(files):
        file = await seed.create_file(ctx, f"scan{offset}-{f}.lif", folder)
        for s in range(series):
            dataset = await seed.create_array_dataset(ctx, f"scan{offset}-{f} S{s}", axes=seed.YX_AXES, shapes=[[32, 32]])
            await sync_to_async(models.FileLink.objects.create)(
                file=file, dataset=dataset, series_identifier=str(s), direction=enums.FileLinkDirectionChoices.SOURCE.value, organization=ctx.request.organization
            )


async def _count(ctx: HttpContext, query: str) -> int:
    """Every query the request ran, on whichever thread a resolver ran it.

    Counted at the cursor rather than with `CaptureQueriesContext`, which sees one
    connection only: the sync resolvers run on another thread's.
    """
    count = 0
    execute = CursorWrapper.execute

    def counting(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202 - a pass-through
        nonlocal count
        count += 1
        return execute(self, *args, **kwargs)

    CursorWrapper.execute = counting
    try:
        result = await schema.execute(query, context_value=ctx)
    finally:
        CursorWrapper.execute = execute
    assert not result.errors, result.errors
    return count


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_files_with_their_series_cost_the_same_at_any_size(authenticated_context: HttpContext) -> None:
    await _files_with_series(authenticated_context, files=2, series=2, offset=0)
    await _count(authenticated_context, FILES)  # the first request also sets up its session
    small = await _count(authenticated_context, FILES)
    await _files_with_series(authenticated_context, files=3, series=4, offset=1)
    large = await _count(authenticated_context, FILES)
    assert small == large, (small, large)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_page_of_dataset_tiles_costs_the_same_at_any_size(authenticated_context: HttpContext) -> None:
    await _files_with_series(authenticated_context, files=1, series=2, offset=0)
    await _count(authenticated_context, DATASETS)  # the first request also sets up its session
    small = await _count(authenticated_context, DATASETS)
    await _files_with_series(authenticated_context, files=2, series=5, offset=1)
    large = await _count(authenticated_context, DATASETS)
    assert small == large, (small, large)
