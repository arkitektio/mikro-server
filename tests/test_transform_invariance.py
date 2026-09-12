"""Which geometric properties survive a map, and how that composes along a path.

The graph already says whether a placement *exists* (`placement`) and how well it is
*known* (`placementValidity`). Neither answers the third question a client actually has
before it reports a number: does a distance, an angle or an area measured on one side
still mean the same thing on the other. An anisotropic calibration -- a z step that is
not the xy pixel size, which is the ordinary microscopy case -- destroys angles while
leaving areas convertible, and nothing in the response distinguished that from a rigid
placement.

So every edge states its invariance class, derived from its `kind` and never stored, and
a layer's is the **minimum** over its path. A minimum for a stronger reason than caution:
the classes are nested groups (isometry inside similarity inside affine), so a
composition belongs to the weakest group any of its factors belongs to.

The classification is deliberately conservative in one direction only. It reads no matrix:
an AFFINE edge reads AFFINE even when its numbers happen to be a rotation, because
separating those needs an SVD, and `is_invertible` already draws that line by declining to
catch a singular affine. Overstating the damage is safe; understating it is not.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed
from tests.test_placement_queries import QueryCounter, _fresh_request

#: A purely spatial grid. `seed.SIMPLE_AXES` leads with a CHANNEL axis, and an "isotropic"
#: scale over that would be scaling an acquisition index -- true of the classifier, which
#: does not look at what an axis means, but a confusing thing for a reader to check against.
SPATIAL_AXES = [
    seed.axis("z", enums.AxisType.SPACE),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

LAYER_INVARIANCE = """
query LayerInvariance($id: ID!) {
  scene(id: $id) {
    layers { id placement placementInvariance placementValidity pathToWorld { transformation { id } } }
  }
}
"""

PATH_TO_WORLD = """
query PathToWorld($id: ID!) {
  scene(id: $id) {
    layers { id placementInvariance pathToWorld { inverted transformation { id kind invariance } } }
  }
}
"""


def _edge(ctx: HttpContext, kind: str, params: dict | None = None, **kwargs) -> models.Transformation:
    """One bare edge, endpoints and all, built through the ORM.

    Directly, not through `createTransformation`: one of the kinds under test (SEQUENCE) is
    refused by that mutation on purpose -- the ingest writes it with its children -- and the
    classifier has to answer for it all the same.
    """
    return models.Transformation.objects.create(
        kind=kind,
        params=params or {},
        creator=ctx.request.user,
        organization=ctx.request.organization,
        **kwargs,
    )


async def _classify(ctx: HttpContext, kind: str, params: dict | None = None, **kwargs) -> str:
    def build_and_classify() -> str:
        return graph_logic.invariance_of(_edge(ctx, kind, params, **kwargs))

    return await sync_to_async(build_and_classify)()


async def _scene_over(ctx: HttpContext, space, lens) -> str:
    """A scene adopting `space` as its world, with one intensity layer over `lens`.

    The replacement for the deleted dataset bootstrap: the dataset is already in this space
    (its calibration edge put it there), so the scene adopts it and nothing is authored.
    """
    scene = await schema.execute(
        "mutation S($input: CreateSceneInput!) { createScene(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"name": "Physical", "coordinateSystem": str(space.pk)}},
    )
    assert not scene.errors, scene.errors
    scene_id = scene.data["createScene"]["id"]

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=ctx,
        variable_values={"input": {"scene": scene_id, "lens": str(lens.pk)}},
    )
    assert not created.errors, created.errors
    return scene_id


async def _layer_field(ctx: HttpContext, scene_id: str, field: str, query: str = LAYER_INVARIANCE):
    result = await schema.execute(query, context_value=_fresh_request(ctx), variable_values={"id": scene_id})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    return layer[field]


# --- per-edge classification ------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["IDENTITY", "TRANSLATION", "ROTATION", "MAP_AXIS"])
async def test_the_isometries_are_the_kinds_that_deform_nothing(authenticated_context: HttpContext, kind: str):
    """A relabelling, an offset and a rotation all leave every distance and angle where it was."""
    assert await _classify(authenticated_context, kind) == "ISOMETRY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_isotropic_scale_is_a_similarity(authenticated_context: HttpContext):
    """One factor on every axis: a circle is still a circle, so angles and length ratios survive."""
    assert await _classify(authenticated_context, "SCALE", {"scale": [0.5, 0.5, 0.5]}) == "SIMILARITY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anisotropic_scale_is_only_affine(authenticated_context: HttpContext):
    """Different factors per axis: a circular bead arrives an ellipse, so no angle survives.

    ABLATION: drop the all-equal check and this reads SIMILARITY -- telling a client that a
    roundness measured in pixels means something in world, which is exactly the silent
    wrong answer the class exists to prevent.
    """
    assert await _classify(authenticated_context, "SCALE", {"scale": [1.0, 0.325, 0.325]}) == "AFFINE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_reads_affine_even_when_its_matrix_is_rigid(authenticated_context: HttpContext):
    """A 90-degree rotation written as a matrix is an isometry, and still reads AFFINE.

    The deliberate limit: proving it rigid needs an SVD, which is numerics inside a metadata
    answer. `is_invertible` stops at the same line, offering a singular affine for inversion
    because only a determinant would catch it. Both err toward claiming less.
    """
    rigid = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0]]
    assert await _classify(authenticated_context, "AFFINE", {"affine": rigid}) == "AFFINE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_field_is_diffeomorphic_at_best(authenticated_context: HttpContext):
    """A map given by an array's values has a position-dependent Jacobian: nothing local transfers."""
    assert await _classify(authenticated_context, "FIELD") == "DIFFEOMORPHIC"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unmappable_edge_corresponds_to_nothing(authenticated_context: HttpContext):
    """The one kind that denies a correspondence denies every property with it."""
    assert await _classify(authenticated_context, "UNMAPPABLE") == "NONE"


def test_an_unknown_kind_fails_safe():
    """A kind the classifier does not know reads NONE, the bottom -- never a claim of rigidity.

    Unsaved, because `kind` is a choices column and the database will not hold a kind that
    does not exist yet. That is the point: this pins what happens when someone *adds* one
    and forgets the table, so a new kind degrades a client's trust rather than inflating it.
    """
    assert graph_logic.invariance_of(models.Transformation(kind="SOME_FUTURE_KIND", params={})) == "NONE"


# --- composites -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scale", "expected"),
    [([2.0, 2.0, 2.0], "SIMILARITY"), ([1.0, 0.325, 0.325], "AFFINE")],
)
async def test_a_sequence_is_the_weakest_of_its_steps(authenticated_context: HttpContext, scale: list, expected: str):
    """A scale-then-translate sequence takes its class from the scale; the translation is rigid."""

    def build() -> str:
        sequence = _edge(authenticated_context, "SEQUENCE")
        _edge(authenticated_context, "SCALE", {"scale": scale}, parent=sequence, order=0)
        _edge(authenticated_context, "TRANSLATION", {"translation": [1.0, 1.0, 1.0]}, parent=sequence, order=1)
        return graph_logic.invariance_of(sequence)

    assert await sync_to_async(build)() == expected


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_childless_wrapper_reads_the_map_it_carries(authenticated_context: HttpContext):
    """A BY_DIMENSION with no children carries its map in its own params, and is read that way.

    The one place this must not mirror `is_invertible`, which answers True for a childless
    wrapper because invertibility does not depend on which params ride along. Invariance is
    nothing but that -- and a childless BY_DIMENSION carrying an `affine` is the ordinary
    shape `build_registration_edge` writes for a registration crossing a rank boundary.

    ABLATION: return ISOMETRY for a childless wrapper and every sheared registration in the
    system reads as rigid.
    """
    assert await _classify(authenticated_context, "BY_DIMENSION", {"affine": [[1.0, 0.5, 10.0], [0.0, 1.0, 20.0]]}) == "AFFINE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_childless_wrapper_with_no_map_is_an_isometry(authenticated_context: HttpContext):
    """Naming axes and nothing else is the identity on the axes named."""
    assert await _classify(authenticated_context, "BY_DIMENSION") == "ISOMETRY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scale", "expected"),
    [([2.0, 2.0], "SIMILARITY"), ([2.0, 3.0], "AFFINE")],
)
async def test_a_childless_wrapper_takes_the_weakest_map_it_carries(authenticated_context: HttpContext, scale: list, expected: str):
    """`_OPTIONAL_PARAMS_BY_KIND` lets one BY_DIMENSION carry several params: the weakest decides.

    ABLATION: return the first match rather than the minimum and an anisotropic scale riding
    beside a translation reads ISOMETRY, from the translation alone.
    """
    assert await _classify(authenticated_context, "BY_DIMENSION", {"scale": scale, "translation": [1.0, 1.0]}) == expected


# --- the path aggregate -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_identity_registration_places_a_layer_isometrically(authenticated_context: HttpContext):
    """Nothing on the path deforms anything, so a distance in the data IS a distance in world."""
    dataset = await seed.create_array_dataset(authenticated_context, "Rigid", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Rigid scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk)}},
    )
    assert not created.errors, created.errors

    assert await _layer_field(authenticated_context, str(scene.pk), "placementInvariance") == "ISOMETRY"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_weakest_edge_on_the_path_decides(authenticated_context: HttpContext):
    """An anisotropic calibration drags an otherwise rigid placement down to AFFINE.

    A **sliced** lens in a scene over the dataset's physical space walks two edges: the crop
    into the pixel grid (a translation -- ISOMETRY, rigid) and the calibration into the
    physical space (unequal pixel sizes -- AFFINE, deforming). One deforming step is enough,
    because the groups nest.

    The second hop is what makes this a test rather than a tautology, and it is what the
    ABLATION bites on: take the *first* edge instead of the minimum and this reads ISOMETRY,
    reporting a z-squashed placement as distance-preserving.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Anisotropic", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    calibration = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[
            seed.physical_axis("z", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
        ],
        scale=[0.5, 0.325, 0.325],
    )
    sliced = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "y", "start": 8, "stop": 40}])
    scene_id = await _scene_over(authenticated_context, calibration, sliced)

    hops = await _layer_field(authenticated_context, scene_id, "pathToWorld")
    assert len(hops) == 2, f"the minimum below asserts nothing over a one-edge path: {hops}"
    assert await _layer_field(authenticated_context, scene_id, "placementInvariance") == "AFFINE", "one anisotropic step decides the whole path"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_isotropic_calibration_keeps_the_layer_similar(authenticated_context: HttpContext):
    """Equal pixel sizes on every axis: shapes and angles survive, and one factor converts lengths."""
    dataset = await seed.create_array_dataset(authenticated_context, "Isotropic", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    calibration = await seed.create_physical_space(
        authenticated_context,
        dataset,
        axes=[
            seed.physical_axis("z", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
        ],
        scale=[0.325, 0.325, 0.325],
    )

    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene_id = await _scene_over(authenticated_context, calibration, lens)

    invariance = await _layer_field(authenticated_context, scene_id, "placementInvariance")
    assert invariance == "SIMILARITY", "a scalar length in scene units is well defined from here up"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unplaced_layer_reads_none_and_says_why_elsewhere(authenticated_context: HttpContext):
    """No path means nothing corresponds -- and `placement` is what distinguishes the two reasons.

    The conflation is deliberate and mirrors `placementValidity`'s UNKNOWN: a client that
    needs to know whether to go looking for a missing registration reads `placement`, not
    this field.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Unplaced", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Empty scene")
    # Registered, layered, then un-registered: placement is explicit, so the layer mutation
    # refuses unplaced data outright and an unplaced layer can only be reached by deleting
    # the claim that placed it -- which is what un-registering *is*.
    edge = await seed.register_into_scene(authenticated_context, scene, dataset)

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk)}},
    )
    assert not created.errors, created.errors

    await sync_to_async(edge.delete)()

    result = await schema.execute(LAYER_INVARIANCE, context_value=_fresh_request(authenticated_context), variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    assert layer["placementInvariance"] == "NONE"
    assert layer["placement"] == "UNREGISTERED", "the field that tells a gap from an impossibility"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_inverted_step_does_not_change_the_class(authenticated_context: HttpContext):
    """Every class here is closed under inversion, which is why the BFS's `inverted` flag needs no handling.

    The edge is authored world -> intrinsic, against the direction a placement walks, so the
    path comes back with an inverted step. The inverse of a rotation is a rotation.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Backwards", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Backwards scene")

    def author_reverse_edge() -> None:
        _edge(
            authenticated_context,
            "ROTATION",
            {"affine": [[0.0, -1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]},
            input=scene.world,
            output=dataset.intrinsic_coordinate_system,
        )

    await sync_to_async(author_reverse_edge)()

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk)}},
    )
    assert not created.errors, created.errors

    result = await schema.execute(PATH_TO_WORLD, context_value=_fresh_request(authenticated_context), variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    (layer,) = result.data["scene"]["layers"]
    assert any(step["inverted"] for step in layer["pathToWorld"]), "the edge points against the walk"
    assert layer["placementInvariance"] == "ISOMETRY"


# --- cost and shape ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_placement_invariance_costs_no_query_beyond_placement_validity(authenticated_context: HttpContext):
    """The class is read off columns and prefetches the graph already fetched.

    `kind` and `params` are local columns of an edge row, and every SceneGraph fetch site
    already prefetches `children` -- so asking for the class adds nothing. A regression here
    means the classification started following a relation, which on a scene of many layers
    is the N+1 that module exists to prevent.
    """
    dataset = await seed.create_array_dataset(authenticated_context, "Counted", axes=SPATIAL_AXES, shapes=[[8, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[])
    scene = await seed.create_scene(authenticated_context, "Counted scene")
    await seed.register_into_scene(authenticated_context, scene, dataset)

    created = await schema.execute(
        "mutation M($input: CreateIntensityLayerInput!) { createIntensityLayer(input: $input) { id } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "lens": str(lens.pk)}},
    )
    assert not created.errors, created.errors

    validity_only = "query Q($id: ID!) { scene(id: $id) { layers { id placementValidity } } }"
    both = "query Q($id: ID!) { scene(id: $id) { layers { id placementValidity placementInvariance } } }"

    counts = []
    for query in (validity_only, both):
        with QueryCounter() as counter:
            result = await schema.execute(query, context_value=_fresh_request(authenticated_context), variable_values={"id": str(scene.pk)})
            assert not result.errors, result.errors
        counts.append(len(counter.queries))

    assert counts[0] == counts[1], f"asking for the invariance cost {counts[1] - counts[0]} extra queries"


def test_the_invariance_is_derived_not_stored():
    """No column, no TextChoices twin -- and the derived fields wear their two distinct names."""
    assert "invariance" not in {field.name for field in models.Transformation._meta.get_fields()}, "a stored class could contradict `params`"
    assert not hasattr(enums, "TransformInvarianceChoices"), "a Django choices twin exists only for a column, and there is no column"

    sdl = schema.as_str()
    assert "enum TransformInvariance" in sdl

    transformation = sdl[sdl.find("interface Transformation ") : sdl.find("\n}", sdl.find("interface Transformation "))]
    assert "invariance: TransformInvariance" in transformation, "the per-edge class lives on the edge"

    layer = sdl[sdl.find("interface Layer ") : sdl.find("\n}", sdl.find("interface Layer "))]
    assert "placementInvariance(" in layer and "): TransformInvariance!" in layer, "the path aggregate lives on the layer, under its own name and taking the coordinate to answer at"
    assert "\n  invariance" not in layer, "the bare word belongs to the edge, not the layer"
