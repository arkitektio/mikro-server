"""`DataArray.to_parent` and `Lens.to_parent` answer about their *own* edge, not any edge.

Both properties resolve the stored map from a level's (or a lens') own voxel space back into the
dataset's intrinsic grid. Under residence a level-0 array and an unsliced lens *share* the
dataset's intrinsic system rather than owning one, so every physical-space edge and every
registration the dataset has also leaves that space. A filter on ``input`` alone therefore
returned whichever of those sorted first under ``Transformation.Meta.ordering`` -- a registration
into a world, presented as the pyramid edge, exactly where both docstrings promise ``None``.

The suite passed with that bug for as long as it existed, because nothing built the shape that
exposes it: a dataset that has *both* levels and a second edge out of intrinsic. These tests build
it.
"""

import pytest
from asgiref.sync import sync_to_async

from core import models
from kante.context import HttpContext
from tests import seed


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_level_zero_has_no_parent_edge_even_when_the_dataset_is_registered(db, authenticated_context: HttpContext):
    """Level 0's space IS intrinsic, so it has no edge -- however many edges leave that space."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32]])

    # The edge that used to be returned in place of "none": a physical space registers the
    # dataset's intrinsic pixels into itself, so it leaves the very space level 0 lives in.
    await seed.create_physical_space(
        ctx,
        dataset,
        axes=[seed.physical_axis("c", seed.enums.AxisType.CHANNEL, "a.u."), seed.physical_axis("y", seed.enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", seed.enums.AxisType.SPACE, "micrometer")],
        scale=[1.0, 0.2, 0.2],
    )

    level_zero = await sync_to_async(lambda: models.DataArray.objects.get(dataset=dataset, level=0))()
    assert await sync_to_async(lambda: level_zero.to_parent)() is None, "level 0 is in the intrinsic grid by definition; the physical-space edge is not its parent edge"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_downsampled_level_returns_its_own_edge(db, authenticated_context: HttpContext):
    """A level that owns a space answers with the edge `create_level_edge` wrote for it."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32]])
    await seed.create_physical_space(
        ctx,
        dataset,
        axes=[seed.physical_axis("c", seed.enums.AxisType.CHANNEL, "a.u."), seed.physical_axis("y", seed.enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", seed.enums.AxisType.SPACE, "micrometer")],
        scale=[1.0, 0.2, 0.2],
    )

    level_one = await sync_to_async(lambda: models.DataArray.objects.get(dataset=dataset, level=1))()
    edge = await sync_to_async(lambda: level_one.to_parent)()

    assert edge is not None
    assert edge.input_id == level_one.coordinate_system_id, "the edge leaves this level's own space"
    assert edge.output_id == dataset.coordinate_system_id, "and lands in the dataset's intrinsic grid"
    assert edge.parent_id is None, "a wrapper's child is a step within an edge, never the edge"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unsliced_lens_has_no_parent_edge(db, authenticated_context: HttpContext):
    """The lens half of the same rule: no slices, no shift, no edge -- and no borrowed one either."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Lensed")
    await seed.create_physical_space(
        ctx,
        dataset,
        axes=[seed.physical_axis("c", seed.enums.AxisType.CHANNEL, "a.u."), seed.physical_axis("y", seed.enums.AxisType.SPACE, "micrometer"), seed.physical_axis("x", seed.enums.AxisType.SPACE, "micrometer")],
        scale=[1.0, 0.2, 0.2],
    )

    lens = await seed.create_lens(ctx, dataset, slices=None)
    assert await sync_to_async(lambda: lens.to_parent)() is None
