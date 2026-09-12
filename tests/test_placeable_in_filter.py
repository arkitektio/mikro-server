"""The `placeableIn` filter: which lenses / table datasets can be composed into a space.

A `Lens`/`TableDataset` is *placeable in* a coordinate system when a traversable path exists
from its own system into that one -- the very gate ``assert_placeable_in`` applies when a
layer is created. The filter walks the transformation edges, so it is a Python-side
reachability question, not an ORM join.

It takes a **space**, not a scene, and the test at the bottom of this file is why: every
scene over one world offers exactly the same candidates, so a scene-shaped argument was
asking the caller for more than the answer depends on. A client holding a scene passes
`scene.worldCoordinateSystem.id`.

The load-bearing test is the consistency one: the batched helper the filter runs over the
whole candidate set must agree, object for object, with the single-source
``is_placeable_in`` -- otherwise the picker would offer a source that layer creation
then refuses (or hide one it would accept). The rest pin the pieces that make that true:
the UNMAPPABLE gate, the descendant closure (a derived dataset placed through its parent's
registration), and the table case.
"""

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test.utils import CaptureQueriesContext
from kante.context import HttpContext

from core import enums, filters, models
from core.logic import graph as graph_logic
from core.mutations import layer as layer_mutations
from mikro_server.schema import schema
from tests import seed

_CREATE_TABLE = """
mutation Create($input: CreateTableDatasetInput!) {
  createTableDataset(input: $input) { id }
}
"""

_COORD_COLUMNS = [
    {"name": "y", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
    {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE"},
]

LENSES = """
query Lenses($space: ID!, $derivedOnly: Boolean, $asLayer: LensLayerKind) {
  lenses(filters: { placeableIn: { space: $space, derivedOnly: $derivedOnly, asLayer: $asLayer } }) { id }
}
"""

TABLE_DATASETS = """
query Tables($space: ID!) {
  tableDatasets(filters: { placeableIn: $space }) { id }
}
"""


async def _table_dataset(ctx: HttpContext, key: str) -> models.TableDataset:
    """A freestanding coordinate table: it owns a placeable (y, x) system, registered on demand."""
    store = await sync_to_async(models.ParquetStore.objects.create)(
        path=f"s3://parquet/{key}", bucket="parquet", key=key, organization=ctx.request.organization, populated=True,
        columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in seed.split_declaration(_COORD_COLUMNS)[0]],
    )
    result = await schema.execute(
        _CREATE_TABLE,
        context_value=ctx,
        variable_values={"input": {"data": str(store.pk), "name": key, **seed.split_payload(_COORD_COLUMNS)}},
    )
    assert not result.errors, result.errors
    return await sync_to_async(models.TableDataset.objects.get)(pk=result.data["createTableDataset"]["id"])


def _derivation(
    ctx: HttpContext,
    child: models.ArrayDataset,
    parent: models.ArrayDataset,
    kind: str,
    value_relation: str | None = None,
) -> models.Transformation:
    """A derivation edge child -> parent (input = child's intrinsic, output = parent's).

    IDENTITY between two c/y/x datasets is a real in-place derivation; UNMAPPABLE records
    "came from that image" while denying any point correspondence -- the edge the placement
    walk refuses. `value_relation` is the other axis of the same edge: CATEGORIZED says the
    values became object ids, which is what makes the child a label map.
    """
    return models.Transformation.objects.create(
        kind=kind,
        input=child.intrinsic_coordinate_system,
        output=parent.intrinsic_coordinate_system,
        organization=ctx.request.organization,
        **({"value_relation": value_relation} if value_relation is not None else {}),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_batched_helper_agrees_with_per_candidate_predicate(authenticated_context: HttpContext):
    """The filter's batched set matches ``is_placeable_in`` object for object.

    Unsliced and sliced lenses over a registered and an unregistered dataset -- the one gap
    the whole design fights is the picker and the layer mutation disagreeing about any of them.
    """
    ctx = authenticated_context
    placed = await seed.create_array_dataset(ctx, "Placed")
    unplaced = await seed.create_array_dataset(ctx, "Unplaced")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, placed)

    lenses = [
        await seed.create_lens(ctx, placed, slices=[]),
        await seed.create_lens(ctx, placed, slices=[{"axis": "y", "start": 8, "stop": 40}]),
        await seed.create_lens(ctx, unplaced, slices=[]),
        await seed.create_lens(ctx, unplaced, slices=[{"axis": "x", "start": 4, "stop": 20}]),
    ]

    def check() -> None:
        dataset_ids = graph_logic.placeable_lens_dataset_ids(scene.world)
        for lens in lenses:
            source = graph_logic.lens_source_system(lens)
            expected = graph_logic.is_placeable_in(scene.world, source)
            assert (lens.dataset_id in dataset_ids) == expected, f"lens {lens.pk} disagrees: batched={lens.dataset_id in dataset_ids}, predicate={expected}"

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_registered_datasets_lenses_are_placeable(authenticated_context: HttpContext):
    """Every lens of a registered dataset -- even a fresh, never-layered one -- is offered; none of an unregistered dataset's are."""
    ctx = authenticated_context
    placed = await seed.create_array_dataset(ctx, "Placed")
    unplaced = await seed.create_array_dataset(ctx, "Unplaced")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, placed)

    fresh = await seed.create_lens(ctx, placed, slices=[])
    sliced = await seed.create_lens(ctx, placed, slices=[{"axis": "y", "start": 8, "stop": 40}])
    orphan = await seed.create_lens(ctx, unplaced, slices=[])

    def check() -> None:
        ids = graph_logic.placeable_lens_dataset_ids(scene.world)
        assert placed.pk in ids
        assert unplaced.pk not in ids

    await sync_to_async(check)()

    result = await schema.execute(LENSES, context_value=ctx, variable_values={"space": str(scene.world_id)})
    assert not result.errors, result.errors
    returned = {lens["id"] for lens in result.data["lenses"]}
    assert str(fresh.pk) in returned
    assert str(sliced.pk) in returned
    assert str(orphan.pk) not in returned


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unmappable_is_excluded_but_a_mappable_descendant_is_placeable(authenticated_context: HttpContext):
    """A mappable derived dataset rides its parent's registration to world; an UNMAPPABLE one does not.

    Registering only the root, a lens of the IDENTITY-derived child is placeable (the
    descendant closure), while a lens of the UNMAPPABLE-derived child is not (the gate).
    """
    ctx = authenticated_context
    root = await seed.create_array_dataset(ctx, "Root")
    mappable = await seed.create_array_dataset(ctx, "MappableChild")
    unmappable = await seed.create_array_dataset(ctx, "UnmappableChild")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, root)

    def wire() -> None:
        _derivation(ctx, mappable, root, enums.TransformKindChoices.IDENTITY.value)
        _derivation(ctx, unmappable, root, enums.TransformKindChoices.UNMAPPABLE.value)

    await sync_to_async(wire)()

    lens_mappable = await seed.create_lens(ctx, mappable, slices=[])
    lens_unmappable = await seed.create_lens(ctx, unmappable, slices=[])

    def check() -> None:
        ids = graph_logic.placeable_lens_dataset_ids(scene.world)
        assert root.pk in ids
        assert mappable.pk in ids, "a mappable descendant is placed through its parent's registration"
        assert unmappable.pk not in ids, "the UNMAPPABLE gate refuses a derivation whose geometry did not survive"
        # Consistency with the single-source predicate, again, on the derived sources.
        for lens, dataset_id in ((lens_mappable, mappable.pk), (lens_unmappable, unmappable.pk)):
            source = graph_logic.lens_source_system(lens)
            assert (dataset_id in ids) == graph_logic.is_placeable_in(scene.world, source)

    await sync_to_async(check)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_table_dataset_placeable_only_once_registered(authenticated_context: HttpContext):
    """A table dataset is offered exactly when its own coordinate system is registered into the scene."""
    ctx = authenticated_context
    placed = await _table_dataset(ctx, "placed-table")
    unplaced = await _table_dataset(ctx, "unplaced-table")
    scene = await seed.create_scene(ctx, "Composition")
    system = await sync_to_async(lambda: placed.coordinate_system)()
    await seed.register_into_scene(ctx, scene, system=system)

    def check() -> None:
        ids = graph_logic.placeable_table_dataset_ids(scene.world)
        assert placed.pk in ids
        assert unplaced.pk not in ids

    await sync_to_async(check)()

    result = await schema.execute(TABLE_DATASETS, context_value=ctx, variable_values={"space": str(scene.world_id)})
    assert not result.errors, result.errors
    returned = {table["id"] for table in result.data["tableDatasets"]}
    assert str(placed.pk) in returned
    assert str(unplaced.pk) not in returned


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_scenes_over_one_world_share_the_placeable_set(authenticated_context: HttpContext):
    """One truth per space: the placeable set is the world's, so every scene over it agrees.

    The registration is a fact about the space, not a per-scene endorsement -- there is
    no membership for it to leak through or be gated by."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Shared")
    scene_a = await seed.create_scene(ctx, "SceneA")

    def make_sibling() -> models.Scene:
        return models.Scene.objects.create(name="SceneB", world=scene_a.world, organization=ctx.request.organization)

    scene_b = await sync_to_async(make_sibling)()
    await seed.register_into_scene(ctx, scene_a, dataset)

    def check() -> None:
        assert dataset.pk in graph_logic.placeable_lens_dataset_ids(scene_a.world)
        assert dataset.pk in graph_logic.placeable_lens_dataset_ids(scene_b.world), "candidates are a property of the space, identical for every scene over it"

    await sync_to_async(check)()


ADATASETS = """
query Datasets($space: ID!) {
  arrayDatasets(filters: { placeableIn: $space }) { id }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_placeable_in_agrees_with_its_lenses(authenticated_context: HttpContext):
    """A dataset is offered exactly when one of its lenses is: same set, one hop up.

    Both read `placeable_lens_dataset_ids`, so the picker cannot offer a dataset whose
    every lens the layer mutation would refuse, nor hide one it would accept.
    """
    ctx = authenticated_context
    placed = await seed.create_array_dataset(ctx, "Placed")
    unplaced = await seed.create_array_dataset(ctx, "Unplaced")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, placed)
    await seed.create_lens(ctx, placed, slices=[])
    await seed.create_lens(ctx, unplaced, slices=[])

    result = await schema.execute(ADATASETS, context_value=ctx, variable_values={"space": str(scene.world_id)})
    assert not result.errors, result.errors
    assert {d["id"] for d in result.data["arrayDatasets"]} == {str(placed.pk)}

    lenses = await schema.execute(LENSES, context_value=ctx, variable_values={"space": str(scene.world_id)})
    assert not lenses.errors, lenses.errors

    def lens_datasets() -> set[str]:
        return {str(models.Lens.objects.get(pk=lens["id"]).dataset_id) for lens in lenses.data["lenses"]}

    assert await sync_to_async(lens_datasets)() == {d["id"] for d in result.data["arrayDatasets"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_derived_dataset_is_placeable_through_its_source(authenticated_context: HttpContext):
    """The descendant closure reaches datasets too: a child is offered on its parent's registration.

    And an UNMAPPABLE derivation is where that stops -- the walk refuses the edge, so the
    child is not offered however much it owes the source historically.
    """
    ctx = authenticated_context
    source = await seed.create_array_dataset(ctx, "Source")
    derived = await seed.create_array_dataset(ctx, "Derived")
    severed = await seed.create_array_dataset(ctx, "Severed")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, source)
    await sync_to_async(_derivation)(ctx, derived, source, enums.TransformKindChoices.IDENTITY.value)
    await sync_to_async(_derivation)(ctx, severed, source, enums.TransformKindChoices.UNMAPPABLE.value)
    for dataset in (source, derived, severed):
        await seed.create_lens(ctx, dataset, slices=[])

    result = await schema.execute(ADATASETS, context_value=ctx, variable_values={"space": str(scene.world_id)})
    assert not result.errors, result.errors
    assert {d["id"] for d in result.data["arrayDatasets"]} == {str(source.pk), str(derived.pk)}


async def _lens_ids(ctx: HttpContext, space_id, **narrowing) -> set[str]:
    """The ids the `lenses` picker returns for a space, optionally narrowed."""
    result = await schema.execute(LENSES, context_value=ctx, variable_values={"space": str(space_id), **narrowing})
    assert not result.errors, result.errors
    return {lens["id"] for lens in result.data["lenses"]}


def _assert_all_placeable(space: models.CoordinateSystem, lens_ids: set[str]) -> None:
    """Every narrowed candidate is still one layer creation would accept.

    The narrowings may only *remove*. A filter that added a candidate `assert_placeable_in`
    refuses would be the exact failure this module exists to prevent, arrived at from the
    other direction.
    """
    for lens_id in lens_ids:
        lens = models.Lens.objects.get(pk=lens_id)
        assert graph_logic.is_placeable_in(space, graph_logic.lens_source_system(lens)), f"lens {lens_id} was offered but is not placeable"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_derived_only_keeps_the_segmentation_and_drops_the_registered_image(authenticated_context: HttpContext):
    """`derivedOnly` is the descendant closure without its seeds: what rode a parent's registration here."""
    ctx = authenticated_context
    source = await seed.create_array_dataset(ctx, "Source")
    derived = await seed.create_array_dataset(ctx, "Derived")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, source)
    await sync_to_async(_derivation)(ctx, derived, source, enums.TransformKindChoices.IDENTITY.value)

    source_lens = await seed.create_lens(ctx, source, slices=[])
    derived_lens = await seed.create_lens(ctx, derived, slices=[])

    assert await _lens_ids(ctx, scene.world_id) == {str(source_lens.pk), str(derived_lens.pk)}
    narrowed = await _lens_ids(ctx, scene.world_id, derivedOnly=True)
    assert narrowed == {str(derived_lens.pk)}, "the registered image needs no lineage tree, so it is not what `derivedOnly` asks for"

    await sync_to_async(_assert_all_placeable)(scene.world, narrowed)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_derived_only_drops_a_dataset_that_is_registered_as_well_as_derived(authenticated_context: HttpContext):
    """Registered *and* derived is still registered: it does not need its lineage to be placeable.

    The rule is "needed a lineage tree to get here", not "has a lineage". `_derivation_descendants`
    already enforces it by seeding `seen` with the registered containers, which is why there is
    no set subtraction to get wrong.
    """
    ctx = authenticated_context
    source = await seed.create_array_dataset(ctx, "Source")
    both = await seed.create_array_dataset(ctx, "RegisteredAndDerived")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, source)
    await seed.register_into_scene(ctx, scene, both)
    await sync_to_async(_derivation)(ctx, both, source, enums.TransformKindChoices.IDENTITY.value)

    source_lens = await seed.create_lens(ctx, source, slices=[])
    both_lens = await seed.create_lens(ctx, both, slices=[])

    assert await _lens_ids(ctx, scene.world_id) == {str(source_lens.pk), str(both_lens.pk)}
    assert await _lens_ids(ctx, scene.world_id, derivedOnly=True) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_as_layer_label_offers_only_the_categorized_derivation(authenticated_context: HttpContext):
    """`asLayer: LABEL` reads the primary derivation's CATEGORIZED -- the signal `_infer_kind` reads.

    `IMAGE` deliberately does not exclude the mask: a label map drawn through a render graph
    is something `createLayer` accepts, so the picker for it must offer one.
    """
    ctx = authenticated_context
    root = await seed.create_array_dataset(ctx, "Root")
    mask = await seed.create_array_dataset(ctx, "Segmentation")
    deconvolved = await seed.create_array_dataset(ctx, "Deconvolved")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, root)

    def wire() -> None:
        _derivation(ctx, mask, root, enums.TransformKindChoices.IDENTITY.value, enums.ValueRelationChoices.CATEGORIZED.value)
        _derivation(ctx, deconvolved, root, enums.TransformKindChoices.IDENTITY.value)

    await sync_to_async(wire)()

    root_lens = await seed.create_lens(ctx, root, slices=[])
    mask_lens = await seed.create_lens(ctx, mask, slices=[])
    deconvolved_lens = await seed.create_lens(ctx, deconvolved, slices=[])
    every = {str(root_lens.pk), str(mask_lens.pk), str(deconvolved_lens.pk)}

    assert await _lens_ids(ctx, scene.world_id) == every
    labels = await _lens_ids(ctx, scene.world_id, asLayer="LABEL")
    assert labels == {str(mask_lens.pk)}
    assert await _lens_ids(ctx, scene.world_id, asLayer="IMAGE") == every, "a mask is drawable as an image, and `createLayer` would accept it"

    # And the two narrowings compose: the mask is derived *and* categorized.
    assert await _lens_ids(ctx, scene.world_id, asLayer="LABEL", derivedOnly=True) == {str(mask_lens.pk)}
    await sync_to_async(_assert_all_placeable)(scene.world, labels)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_cropped_to_one_column_is_offered_for_no_layer_kind(authenticated_context: HttpContext):
    """`asLayer` applies the renderability gate layer creation applies, and it is per lens.

    The dataset is placeable and its unsliced lens is drawable; the one sliced down to a
    single x column is not, and `assert_renderable` would refuse it. Omitting `asLayer`
    still offers it -- that filter answers a spatial question, not a drawing one.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Placed")
    scene = await seed.create_scene(ctx, "Composition")
    await seed.register_into_scene(ctx, scene, dataset)

    whole = await seed.create_lens(ctx, dataset, slices=[])
    sliver = await seed.create_lens(ctx, dataset, slices=[{"axis": "x", "start": 0, "stop": 1}])

    assert await _lens_ids(ctx, scene.world_id) == {str(whole.pk), str(sliver.pk)}
    assert await _lens_ids(ctx, scene.world_id, asLayer="IMAGE") == {str(whole.pk)}

    def refuses() -> None:
        # `ValueError`, not `AssertionError`, and the type is the whole point: these were two
        # bare `assert`s until 2026-08-21, which `python -O` strips -- so the gate vanished in
        # exactly the deployment most likely to run that way. Asserting on the *type* is what
        # makes this test fail if they ever come back; the message would read the same either
        # way. Run this module under `-O` to check it for real.
        with pytest.raises(ValueError) as raised:
            layer_mutations.assert_renderable(models.Lens.objects.get(pk=sliver.pk))
        assert not isinstance(raised.value, AssertionError)
        assert "must both have more than one pixel" in str(raised.value)

    await sync_to_async(refuses)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_renderability_pass_does_not_grow_with_the_number_of_lenses(authenticated_context: HttpContext):
    """`asLayer` costs a constant number of queries, not two per candidate lens.

    `Lens.axis_specs` walks the dataset's coordinate system's axes and `ArrayDataset.shape_list`
    orders the data arrays, so the obvious loop is two queries a lens and no `select_related`
    fixes it. The helper batches all three fetches instead, and this is what says so.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Composition")
    datasets = [await seed.create_array_dataset(ctx, f"Placed{index}") for index in range(2)]
    for dataset in datasets:
        await seed.register_into_scene(ctx, scene, dataset)
        for _ in range(3):
            await seed.create_lens(ctx, dataset, slices=[])

    def measure() -> int:
        dataset_ids = {dataset.pk for dataset in datasets}
        with CaptureQueriesContext(connection) as captured:
            assert len(filters._renderable_lens_ids(dataset_ids)) == 6
        return len(captured)

    assert await sync_to_async(measure)() == 3, "one fetch of the lenses, one of the axes, one of the arrays"
