"""Which lenses overlap an annotation, wherever the two are co-registered.

`LensFilter.overlapsAnnotation` has no SQL form: a lens' extent is slice arithmetic over JSON,
and a lens on another dataset relates to the annotation only through edges whose composed
paths are never stored. So it is a walk, and the second half of this module is what holds its
price down: one graph per space the annotation reaches, flat in everything else.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_placement_queries import QueryCounter, _fresh_request

CREATE_COLLECTION = """
mutation M($input: CreateAnnotationCollectionInput!) {
  createAnnotationCollection(input: $input) { id }
}
"""

CREATE = """
mutation Create($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id }
}
"""

OVERLAPPING = """
query Overlapping($annotation: ID!) {
  lenses(filters: { overlapsAnnotation: $annotation }) { id }
}
"""

#: A rectangle on rows and columns 10 to 20, as corners in a (y, x) drawing space.
SQUARE = [[10.0, 10.0], [10.0, 20.0], [20.0, 20.0], [20.0, 10.0]]

#: The same square on plane 2 of a (z, y, x) space.
SQUARE_ZYX = [[2.0, *corner] for corner in SQUARE]

NEAR = [{"axis": "y", "start": 0, "stop": 32}]
FAR = [{"axis": "y", "start": 40, "stop": 64}]


async def _collection_over(ctx: HttpContext, dataset, *, name: str = "Traced") -> str:
    """A (y, x) drawing space over a dataset, joined by the edge that states the rank change."""
    result = await schema.execute(
        CREATE_COLLECTION,
        context_value=ctx,
        variable_values={
            "input": {
                "name": name,
                "axes": [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
                "derivedFrom": [{"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "BY_DIMENSION", "inputAxes": ["y", "x"], "outputAxes": ["y", "x"]}}],
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createAnnotationCollection"]["id"]


async def _draw(ctx: HttpContext, vectors: list[list[float]], *, collection: str | None = None, scene: str | None = None, coordinates: list[dict] | None = None) -> str:
    """One rectangle, into a collection or onto a scene."""
    target = {"collection": collection} if collection else {"scene": scene}
    result = await schema.execute(CREATE, context_value=ctx, variable_values={"input": {**target, "kind": "RECTANGLE", "vectors": vectors, "coordinates": coordinates or []}})
    assert not result.errors, result.errors
    return result.data["createAnnotation"]["id"]


async def _overlapping(ctx: HttpContext, annotation_id: str) -> set[str]:
    result = await schema.execute(OVERLAPPING, context_value=_fresh_request(ctx), variable_values={"annotation": annotation_id})
    assert not result.errors, result.errors
    return {lens["id"] for lens in result.data["lenses"]}


async def _cost(ctx: HttpContext, annotation_id: str) -> tuple[set[str], int]:
    """The answer and the number of SQL statements one steady-state request pays for it."""
    await _overlapping(ctx, annotation_id)
    with QueryCounter() as counter:
        found = await _overlapping(ctx, annotation_id)
    return found, len(counter)


# --- what overlaps ----------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lenses_over_the_annotated_dataset_are_told_apart_by_their_slices(authenticated_context: HttpContext):
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Image")
    whole = await seed.create_lens(ctx, dataset)
    near = await seed.create_lens(ctx, dataset, slices=NEAR)
    far = await seed.create_lens(ctx, dataset, slices=FAR)
    annotation = await _draw(ctx, SQUARE, collection=await _collection_over(ctx, dataset))

    found = await _overlapping(ctx, annotation)

    assert found == {str(whole.pk), str(near.pk)}
    assert str(far.pk) not in found, "rows 40 to 64 do not meet a shape on rows 10 to 20"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_over_another_dataset_overlaps_through_a_shared_world(authenticated_context: HttpContext):
    """The annotation is drawn on one dataset; the lenses look at another registered beside it."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    drawn_on = await seed.create_array_dataset(ctx, "DrawnOn", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    beside = await seed.create_array_dataset(ctx, "Beside", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    apart = await seed.create_array_dataset(ctx, "Apart", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    for dataset in (drawn_on, beside):
        await seed.register_into_scene(ctx, scene, dataset)

    meets = await seed.create_lens(ctx, beside, slices=NEAR)
    misses = await seed.create_lens(ctx, beside, slices=FAR)
    unregistered = await seed.create_lens(ctx, apart)
    annotation = await _draw(ctx, SQUARE, collection=await _collection_over(ctx, drawn_on))

    found = await _overlapping(ctx, annotation)

    assert str(meets.pk) in found
    assert str(misses.pk) not in found
    assert str(unregistered.pk) not in found, "a dataset in no space the annotation reaches is not co-registered with it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_annotation_drawn_onto_a_scene_overlaps_the_lenses_placed_in_it(authenticated_context: HttpContext):
    """A scene's own collection is registered straight into the world, with no dataset under it."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Canvas")
    dataset = await seed.create_array_dataset(ctx, "Volume", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    await seed.register_into_scene(ctx, scene, dataset)
    near = await seed.create_lens(ctx, dataset, slices=NEAR)
    far = await seed.create_lens(ctx, dataset, slices=FAR)
    off_plane = await seed.create_lens(ctx, dataset, slices=[{"axis": "z", "start": 5, "stop": 8}])

    annotation = await _draw(ctx, SQUARE_ZYX, scene=str(scene.id))

    assert await _overlapping(ctx, annotation) == {str(near.pk)}, (far.pk, off_plane.pk)

    # A flat shape in a volume says which plane with a pin, and with none it is on all of them.
    low_plane = await _draw(ctx, SQUARE, scene=str(scene.id), coordinates=[{"name": "z", "value": 2}])
    every_plane = await _draw(ctx, SQUARE, scene=str(scene.id))
    assert await _overlapping(ctx, low_plane) == {str(near.pk)}
    assert await _overlapping(ctx, every_plane) == {str(near.pk), str(off_plane.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_pinned_coordinate_keeps_the_annotation_off_the_other_channels(authenticated_context: HttpContext):
    """A (y, x) shape says nothing about c; its `coordinates` do, and the neighbouring channel must not match."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels")
    first = await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}])
    second = await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 1, "stop": 2}])
    collection = await _collection_over(ctx, dataset)

    pinned = await _draw(ctx, SQUARE, collection=collection, coordinates=[{"name": "c", "value": 0}])
    unpinned = await _draw(ctx, SQUARE, collection=collection)

    assert await _overlapping(ctx, pinned) == {str(first.pk)}
    assert await _overlapping(ctx, unpinned) == {str(first.pk), str(second.pk)}, "a coordinate the annotation does not pin is one it spans"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_placed_only_per_channel_is_not_returned(authenticated_context: HttpContext):
    """Where it sits depends on a channel nobody named, so it has not been shown to overlap."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Chromatic")
    dataset = await seed.create_array_dataset(ctx, "Stack")
    lens = await seed.create_lens(ctx, dataset)
    registration = await seed.register_into_scene(ctx, scene, dataset)
    annotation = await _draw(ctx, SQUARE_ZYX, scene=str(scene.id))

    assert await _overlapping(ctx, annotation) == {str(lens.pk)}

    def scope() -> None:
        registration.selector = {"axis": "c", "index": 2}
        registration.save()

    await sync_to_async(scope)()

    assert await _overlapping(ctx, annotation) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_filter_stays_inside_the_organization(authenticated_context: HttpContext, other_org_context: HttpContext):
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Shared")
    mine = await seed.create_array_dataset(ctx, "Mine", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    theirs = await seed.create_array_dataset(other_org_context, "Theirs", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    for dataset in (mine, theirs):
        await seed.register_into_scene(ctx, scene, dataset)
    my_lens = await seed.create_lens(ctx, mine)
    their_lens = await seed.create_lens(other_org_context, theirs)
    annotation = await _draw(ctx, SQUARE_ZYX, scene=str(scene.id))

    assert await _overlapping(ctx, annotation) == {str(my_lens.pk)}, their_lens.pk
    assert await _overlapping(other_org_context, annotation) == set(), "another organization's annotation names nothing here"


# --- cost -------------------------------------------------------------------------


async def _world_with(ctx: HttpContext, name: str, *, datasets: int, lenses: int) -> str:
    """A scene of `datasets` volumes, `lenses` sliced lenses each, and one annotation drawn onto it."""
    scene = await seed.create_scene(ctx, name)
    for index in range(datasets):
        dataset = await seed.create_array_dataset(ctx, f"{name}-{index}", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
        await seed.register_into_scene(ctx, scene, dataset)
        for _ in range(lenses):
            await seed.create_lens(ctx, dataset, slices=NEAR)
    return await _draw(ctx, SQUARE_ZYX, scene=str(scene.id))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_cost_does_not_grow_with_the_datasets_or_their_lenses(authenticated_context: HttpContext):
    """Flat in both: the residents arrive with everything a lens' extent is made of.

    The lens count is the one that would have grown. `Lens.shape_list` is two queries a lens,
    and it is what the graph's own `_resident_box` reads.
    """
    ctx = authenticated_context
    small = await _world_with(ctx, "Small", datasets=2, lenses=1)
    wide = await _world_with(ctx, "Wide", datasets=6, lenses=1)
    deep = await _world_with(ctx, "Deep", datasets=2, lenses=4)

    (small_found, small_cost), (wide_found, wide_cost), (deep_found, deep_cost) = [await _cost(ctx, annotation) for annotation in (small, wide, deep)]

    assert (len(small_found), len(wide_found), len(deep_found)) == (2, 6, 8)
    assert wide_cost == small_cost, f"the cost grew with the datasets: {small_cost} for 2, {wide_cost} for 6"
    assert deep_cost == small_cost, f"the cost grew with the lenses: {small_cost} for 1 each, {deep_cost} for 4 each"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_cost_is_one_graph_per_space_the_annotation_reaches(authenticated_context: HttpContext):
    """What it does grow with, stated: every further space the drawn-on dataset sits in is another walk."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Volume", axes=seed.ZYX_AXES, shapes=[[8, 64, 64]])
    lens = await seed.create_lens(ctx, dataset, slices=NEAR)
    annotation = await _draw(ctx, SQUARE, collection=await _collection_over(ctx, dataset))

    costs = []
    for name in ("alone", "First", "Second"):
        if name != "alone":
            await seed.register_into_scene(ctx, await seed.create_scene(ctx, name), dataset)
        found, cost = await _cost(ctx, annotation)
        assert found == {str(lens.pk)}
        costs.append(cost)

    alone, one_world, two_worlds = costs
    assert alone < one_world < two_worlds, costs
    assert two_worlds - one_world <= one_world - alone, f"a second world cost more than the first did: {costs}"
