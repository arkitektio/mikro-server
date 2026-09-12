"""A fusion has several parents, and the creator's declared order says which one places it.

A merge of two channels, a stitch of two tiles: the result was computed from several
lenses, and each of those relations is a spatial fact -- so each is an edge, exactly as a
single-source derivation is. What multi-parent adds is the question single-parent never
had to answer: which parent places the fused data? The answer is not a pk accident but a
declaration -- the first `derivedFrom` entry is the primary parent, and it drives the
lineage root, the default registration and the order `derivedFrom` reports.

These tests pin the three halves of that contract: the order is stored and reported as
declared, the placement machinery acts on the primary while still *walking* every parent,
and creation refuses the inputs that would make the primary a lie (a duplicate source, or
an UNMAPPABLE entry hiding a mappable parent behind it).
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_derived_datasets import _derive

CREATE_SCENE = """
mutation CreateScene($input: CreateSceneInput!) {
  createScene(input: $input) { id worldCoordinateSystem { id } }
}
"""

REGISTER = """
mutation Register($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id kind }
}
"""

MAKE_LAYER = """
mutation Make($input: CreateIntensityLayerInput!) {
  createIntensityLayer(input: $input) { id }
}
"""

PLACEMENT = """
query Placement($id: ID!) {
  scene(id: $id) {
    layers { id pathToWorld { transformation { id kind input { id  } output { id residents { __typename } } } } }
  }
}
"""

DERIVED = """
query Derived($id: ID!) {
  arrayDataset(id: $id) {
    id
    derivedFrom { id kind output { id } }
  }
}
"""

_AFFINE_3D = [
    [1.0, 0.0, 0.0, 5.0],
    [0.0, 1.0, 0.0, 5.0],
    [0.0, 0.0, 1.0, 0.0],
]


async def _two_sources(ctx: HttpContext) -> tuple[models.ArrayDataset, models.Lens, models.ArrayDataset, models.Lens]:
    """Two acquired datasets with a full lens each -- the parents of every fusion below."""
    left = await seed.create_array_dataset(ctx, "Left")
    left_lens = await seed.create_lens(ctx, left, slices=[])
    right = await seed.create_array_dataset(ctx, "Right")
    right_lens = await seed.create_lens(ctx, right, slices=[])
    return left, left_lens, right, right_lens


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_fusion_records_every_parent_in_declared_order(authenticated_context: HttpContext):
    """`derivedFrom` is the declared list, not a pk lottery: swap the input and the order swaps."""
    _, left_lens, _, right_lens = await _two_sources(authenticated_context)

    async def fuse(name: str, first: models.Lens, second: models.Lens):
        result = await _derive(
            authenticated_context,
            name,
            axes=seed.SIMPLE_AXES,
            shape=[3, 64, 64],
            entries=[{"kind": "LENS", "lens": str(first.pk), "transform": {"kind": "IDENTITY"}}, {"kind": "LENS", "lens": str(second.pk), "transform": {"kind": "IDENTITY"}}],
        )
        assert not result.errors, result.errors
        return result.data["createArrayDataset"]["id"]

    fused_id = await fuse("Fused", left_lens, right_lens)
    swapped_id = await fuse("Swapped", right_lens, left_lens)

    left_space = str((await sync_to_async(lambda: left_lens.space)()).pk)
    right_space = str((await sync_to_async(lambda: right_lens.space)()).pk)

    for dataset_id, expected in ((fused_id, [left_space, right_space]), (swapped_id, [right_space, left_space])):
        result = await schema.execute(DERIVED, context_value=authenticated_context, variable_values={"id": dataset_id})
        assert not result.errors, result.errors
        edges = result.data["arrayDataset"]["derivedFrom"]
        assert [edge["output"]["id"] for edge in edges] == expected, "the first entry is the primary parent, and only the creator says which that is"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_duplicate_source_is_refused(authenticated_context: HttpContext):
    """One entry per source: a second entry for the same lens is two claims about one relation."""
    _, left_lens, _, _ = await _two_sources(authenticated_context)

    result = await _derive(
        authenticated_context,
        "DoubleCounted",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[{"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "IDENTITY"}}, {"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "IDENTITY"}}],
    )
    assert result.errors, "the same lens twice is not a fusion, it is a contradiction waiting to be written"
    # "source", not "lens": the duplicate check keys on (kind, id) now, so it catches two
    # entries naming the same table as readily as two naming the same lens -- and does not
    # collide two different kinds of source that happen to share a numeric id.
    assert "distinct source" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_entry_may_not_hide_a_mappable_parent(authenticated_context: HttpContext):
    """The primary is the placing parent, so an UNMAPPABLE first entry ahead of a mappable one is refused.

    The walks refuse an UNMAPPABLE primary -- that is its contract -- so a mappable parent
    behind it would be recorded and never placed by. Reversed, the same pair is fine: the
    mappable parent places, the UNMAPPABLE one records the history that did not survive.
    """
    _, left_lens, _, right_lens = await _two_sources(authenticated_context)

    refused = await _derive(
        authenticated_context,
        "HiddenParent",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[
            {"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "UNMAPPABLE", "reason": "geometry lost"}},
            {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "IDENTITY"}},
        ],
    )
    assert refused.errors, "a mappable parent must not hide behind an UNMAPPABLE primary"
    assert "primary" in str(refused.errors[0])

    accepted = await _derive(
        authenticated_context,
        "DeclaredParent",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[
            {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "UNMAPPABLE", "reason": "geometry lost"}},
        ],
    )
    assert not accepted.errors, accepted.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_all_unmappable_fusion_is_a_root_and_its_layer_is_refused_as_unmappable(authenticated_context: HttpContext):
    """History from several sources, geometry from none: the data is its own root, and its
    layer is refused with the impossibility message -- not the go-author-a-registration one,
    because there is no missing registration to author."""
    _, left_lens, _, right_lens = await _two_sources(authenticated_context)

    derived = await _derive(
        authenticated_context,
        "Measurements",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[
            {"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "UNMAPPABLE", "reason": "reduced away"}},
            {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "UNMAPPABLE", "reason": "reduced away"}},
        ],
    )
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    ancestors = await sync_to_async(graph_logic.lineage_ancestors)(dataset)
    root = await sync_to_async(graph_logic.primary_lineage_root)(dataset)
    assert ancestors == [], "no parent maps, so none places -- the spatial lineage is empty"
    assert root.pk == dataset.pk

    scene_result = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "Sc"}})
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert made.errors, "data no parent can place cannot be composed into a shared scene"
    assert "UNMAPPABLE" in str(made.errors[0]), "the error says nothing can place this, rather than sending someone to author an edge"
    assert "createTransformation" not in str(made.errors[0])

    def claims_into_world() -> int:
        world_id = models.Scene.objects.get(pk=scene_id).world_id
        return models.Transformation.objects.filter(parent__isnull=True, output_id=world_id).count()

    assert await sync_to_async(claims_into_world)() == 0, "and nothing was fabricated on the way out"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lineage_is_the_primary_chain_and_history_keeps_every_parent(authenticated_context: HttpContext):
    """`lineage_ancestors` is the fact tree's chain; `derivation_edges` is the full history.

    A fusion owes both parents historically, but it *sits* where its primary sits: the
    spatial lineage walks the first-declared edge only, and the second parent is a
    recorded fact that never places.
    """
    left, left_lens, right, right_lens = await _two_sources(authenticated_context)

    derived = await _derive(
        authenticated_context,
        "Fused",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[{"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "IDENTITY"}}, {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "IDENTITY"}}],
    )
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])

    ancestors = await sync_to_async(graph_logic.lineage_ancestors)(dataset)
    assert [ancestor.pk for ancestor in ancestors] == [left.pk], "the spatial lineage is the primary chain: the fusion sits where its first-declared parent sits"

    edges = await sync_to_async(graph_logic.derivation_edges)(dataset)
    assert len(edges) == 2, "history is not placement: derivedFrom still reports every parent"

    root = await sync_to_async(graph_logic.primary_lineage_root)(dataset)
    assert root.pk == left.pk, "the root is reached by taking the FIRST edge at every hop, not any edge"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unregistered_fusion_is_rejected_and_nothing_is_written(authenticated_context: HttpContext):
    """No parent is registered, so the fusion's layer is refused -- and the graph is untouched.

    The server used to pin the primary parent here. It no longer writes anything: two
    stitched tiles both landing at the world origin was always a fabrication, and now the
    creator authors the registration that is actually true (about whichever parent they
    measured) before the layer goes in.
    """
    left, left_lens, right, right_lens = await _two_sources(authenticated_context)

    derived = await _derive(
        authenticated_context,
        "Fused",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[{"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "IDENTITY"}}, {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "IDENTITY"}}],
    )
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    scene_result = await schema.execute(CREATE_SCENE, context_value=authenticated_context, variable_values={"input": {"name": "Sc"}})
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]
    edge_count = await sync_to_async(models.Transformation.objects.count)()

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert made.errors, "no parent is registered, so the fusion has no path to world"
    assert "createTransformation" in str(made.errors[0])

    assert await sync_to_async(models.Transformation.objects.count)() == edge_count, "the refused mutation wrote no edge at all"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_fusion_places_through_either_parent(authenticated_context: HttpContext):
    """A fusion reaches the world through *whichever* parent is registered (RFC-9).

    The fact tree used to keep one parent edge per system, so registering only the SECONDARY
    parent placed nothing and the fusion had to be re-anchored to move. That rule went with
    `fact_edges`: every derivation edge is walkable now, rivals and all, so a fusion sits
    wherever a parent of it sits and the choice between two routes is the tie-break's, not
    the data's.

    This is the deliberate reversal, not an accident -- it is the same decision that lets a
    space hold rival claims.
    """
    left, left_lens, right, right_lens = await _two_sources(authenticated_context)

    derived = await _derive(
        authenticated_context,
        "Fused",
        axes=seed.SIMPLE_AXES,
        shape=[3, 64, 64],
        entries=[{"kind": "LENS", "lens": str(left_lens.pk), "transform": {"kind": "IDENTITY"}}, {"kind": "LENS", "lens": str(right_lens.pk), "transform": {"kind": "IDENTITY"}}],
    )
    assert not derived.errors, derived.errors
    dataset = await sync_to_async(models.ArrayDataset.objects.get)(pk=derived.data["createArrayDataset"]["id"])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])

    scene_result = await schema.execute(
        CREATE_SCENE,
        context_value=authenticated_context,
        variable_values={"input": {"name": "Sc", "axes": [{"name": "z", "type": "SPACE", "unit": "micrometer"}, {"name": "y", "type": "SPACE", "unit": "micrometer"}, {"name": "x", "type": "SPACE", "unit": "micrometer"}]}},
    )
    assert not scene_result.errors, scene_result.errors
    scene_id = scene_result.data["createScene"]["id"]
    world_id = scene_result.data["createScene"]["worldCoordinateSystem"]["id"]

    right_intrinsic = await sync_to_async(lambda: right.intrinsic_coordinate_system)()
    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={"input": {"input": str(right_intrinsic.pk), "output": world_id, "transform": {"kind": "AFFINE", "affine": _AFFINE_3D}}},
    )
    assert not registered.errors, registered.errors

    placed = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert not placed.errors, f"the secondary parent's registration places the fusion too: {placed.errors}"

    # And registering the primary as well leaves the fusion placed -- two routes now, which
    # is exactly the rival case the tie-break exists to settle.
    left_intrinsic = await sync_to_async(lambda: left.coordinate_system)()
    registered = await schema.execute(
        REGISTER,
        context_value=authenticated_context,
        variable_values={"input": {"input": str(left_intrinsic.pk), "output": world_id, "transform": {"kind": "AFFINE", "affine": _AFFINE_3D}}},
    )
    assert not registered.errors, registered.errors

    made = await schema.execute(MAKE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": scene_id, "lens": str(lens.pk), "intensityAxis": "c"}})
    assert not made.errors, made.errors

    placement = await schema.execute(PLACEMENT, context_value=authenticated_context, variable_values={"id": scene_id})
    assert not placement.errors, placement.errors

    path = placement.data["scene"]["layers"][0]["pathToWorld"]
    assert path is not None, "the primary parent's registration is what places the fusion"
    assert path[-1]["transformation"]["output"]["residents"] == [], "the path ends in a space nothing lives in: a world"
    hops = [step["transformation"]["input"]["id"] for step in path]
    assert str(left_intrinsic.pk) in hops, "the walk goes through the primary parent's intrinsic system"
    assert str(right_intrinsic.pk) not in hops, "and never through the secondary's"


def _best_path(source_pk: int, target_pk: int):
    """Every top-level edge, fetched the way the real universe fetches them, then searched.

    `adjacency_of` reads both endpoints and their axes (through `is_reverse_traversable` ->
    `edge_axis_names`), so the same `select_related`/`prefetch_related` the production fetchers
    use is not an optimisation here -- without it the reads happen lazily, one per edge, inside
    the async test and fail.
    """
    edges = list(
        models.Transformation.objects.filter(parent__isnull=True)
        .select_related("input", "output")
        .prefetch_related("children", "input__axes", "output__axes")
    )
    return graph_logic._bfs_path(graph_logic.adjacency_of(edges), source_pk, target_pk)

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_validated_route_beats_a_shorter_unknown_one(authenticated_context: HttpContext):
    """The rival case the tie-break exists to settle, now actually settled.

    The walk used to be an unweighted BFS: fewest hops, ties by edge pk, and `validity` never
    read during the search. So the *first-authored* one-hop UNKNOWN registration won over a
    two-hop chain of VALIDATED ones -- and `placementValidity` then dutifully reported UNKNOWN.
    The system chose the worse answer and said so.

    The rival is authored **first**, so it also holds the lower pk: under the old rule it won on
    both counts. It must now lose on the one that matters.
    """
    ctx = seed._creation(authenticated_context)

    def space(name: str) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_pixel_axes(made, seed.YX_AXES)
        return made

    source, midpoint, world = await sync_to_async(space)("Source"), await sync_to_async(space)("Midpoint"), await sync_to_async(space)("World")

    # Authored first, so it is both the shortest route and the lowest pk.
    guess = await sync_to_async(graph_logic.build_registration_edge)(
        input_system=source, output_system=world, kind="TRANSLATION", translation=[9.0, 9.0],
        validity=enums.PlacementValidity.UNKNOWN.value, ctx=ctx,
    )
    # The longer, better-known route.
    await sync_to_async(graph_logic.build_registration_edge)(
        input_system=source, output_system=midpoint, kind="TRANSLATION", translation=[1.0, 1.0],
        validity=enums.PlacementValidity.VALIDATED.value, ctx=ctx,
    )
    await sync_to_async(graph_logic.build_registration_edge)(
        input_system=midpoint, output_system=world, kind="TRANSLATION", translation=[2.0, 2.0],
        validity=enums.PlacementValidity.VALIDATED.value, ctx=ctx,
    )

    path = await sync_to_async(_best_path)(source.pk, world.pk)

    assert path is not None
    assert len(path) == 2, "the two-hop VALIDATED chain wins, though it is longer and later"
    assert guess.pk not in {edge.pk for edge, _ in path}, "the shorter UNKNOWN guess is not the route"
    assert graph_logic.weakest_validity([edge.validity for edge, _ in path]) == enums.PlacementValidityChoices.VALIDATED.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_among_equally_known_routes_the_shortest_still_wins(authenticated_context: HttpContext):
    """Validity is the first key, not the only one: hops still break its ties."""
    ctx = seed._creation(authenticated_context)

    def space(name: str) -> models.CoordinateSystem:
        made = models.CoordinateSystem.objects.create(name=name, creator=ctx.user, organization=ctx.organization)
        graph_logic.create_pixel_axes(made, seed.YX_AXES)
        return made

    source, midpoint, world = await sync_to_async(space)("Source"), await sync_to_async(space)("Midpoint"), await sync_to_async(space)("World")

    for a, b in ((source, midpoint), (midpoint, world), (source, world)):
        await sync_to_async(graph_logic.build_registration_edge)(
            input_system=a, output_system=b, kind="TRANSLATION", translation=[1.0, 1.0],
            validity=enums.PlacementValidity.VALIDATED.value, ctx=ctx,
        )

    path = await sync_to_async(_best_path)(source.pk, world.pk)
    assert path is not None and len(path) == 1, "all three edges are equally known, so the direct one wins on hops"
