"""A composite edge is one row: a SEQUENCE carries its steps, a BY_DIMENSION carries its map.

Both used to be trees of `Transformation` rows. A SEQUENCE's scale and translation were child
rows with no endpoints, with ids a client could refine one at a time -- writing the history on
the child, so the wrapper's `version` never moved -- and a BY_DIMENSION's map was stored in its
own `params` *and* projected as children so a client could read the numbers at all, because
`ByDimensionTransformation` published no `affine`. Measured on the Visium HD ingest: a 412x400
bin lattice registered onto an H&E slide by a fitted affine (pitch 58.43 px, rotation 0.38 deg)
should span 24 073 x 23 372 px; it drew 412 x 400 px at the origin, because every client-side
composer folded over an empty child list.

Now the numbers are where the type publishes them -- `steps` on a sequence, `scale` /
`translation` / `affine` on a by-dimension -- and a refinement writes the row a client reads.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from pytest import approx

from core import enums, models
from core.creation import CreationContext
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from mikro_server.schema import schema
from tests import seed
from tests.seed import axis

#: The real 016um Visium HD lattice fit: rows (y, x), columns (row, col, 1). Its linear part is
#: symmetric and its determinant negative -- a rotation with a reflection -- so a transposed or
#: sign-dropped copy of it still looks plausible. That is exactly why the numbers have to travel.
LATTICE = [
    [-58.4281240, -0.389111, 24102.7386],
    [-0.3888420, 58.4299340, 257.103715],
]

REGISTRATIONS = """
query Registrations($id: ID!) {
  coordinateSystem(id: $id) {
    registrations {
      id kind version inputAxes outputAxes invariance
      ... on ByDimensionTransformation { scale translation affine }
    }
  }
}
"""

UPDATE = """
mutation Update($input: UpdateTransformationInput!) {
  updateTransformation(input: $input) { id version ... on ByDimensionTransformation { scale } }
}
"""


def _creation(ctx: HttpContext) -> CreationContext:
    return CreationContext(user=ctx.request._user, organization=ctx.request._organization, membership=ctx.request._membership, task=None)


async def _systems(ctx: HttpContext) -> tuple[models.CoordinateSystem, models.CoordinateSystem]:
    """Two array datasets' intrinsic systems: a (row, col) lattice and a (c, y, x) slide."""
    lattice = await seed.create_array_dataset(ctx, "Lattice", axes=[axis("row", enums.AxisType.SPACE), axis("col", enums.AxisType.SPACE)], shapes=[[412, 400]])
    slide = await seed.create_array_dataset(ctx, "Slide", shapes=[[3, 21943, 23618]])
    return (await sync_to_async(lambda: lattice.coordinate_system)(), await sync_to_async(lambda: slide.coordinate_system)())


def _edge(ctx: HttpContext, input_system: models.CoordinateSystem, output_system: models.CoordinateSystem, **params: object) -> models.Transformation:
    return graph_logic.build_registration_edge(input_system=input_system, output_system=output_system, kind=enums.TransformKind.BY_DIMENSION.value, input_axes=["row", "col"], output_axes=["y", "x"], ctx=_creation(ctx), **params)


async def _registration(ctx: HttpContext, system: models.CoordinateSystem) -> dict:
    result = await schema.execute(REGISTRATIONS, context_value=ctx, variable_values={"id": str(system.pk)})
    assert not result.errors, result.errors
    (edge,) = result.data["coordinateSystem"]["registrations"]
    return edge


def _sub_matrix(edge: models.Transformation) -> list[list[float]]:
    return coords_logic._sub_matrix(graph_logic._edge_step(edge))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_reaches_a_client_off_the_edge_itself(authenticated_context: HttpContext) -> None:
    """The matrix is on the row, in the type that publishes it, verbatim -- and nothing else is written."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, affine=LATTICE)

    published = await _registration(authenticated_context, slide)
    # Verbatim, not multiplied into something equivalent: a client rendering the reflection
    # as a rotation is the failure this exists to stop, and rounding it would hide that.
    assert published["affine"] == LATTICE
    assert published["scale"] == [] and published["translation"] == []
    assert edge.steps == [], "a by-dimension has no steps: its map is its params"
    assert await models.Transformation.objects.acount() == 1, "one edge, one row"

    composed = await sync_to_async(_sub_matrix)(edge)
    assert composed[0][:3] == approx(LATTICE[0])
    assert composed[1][:3] == approx(LATTICE[1])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scale_and_translation_read_back_and_compose_in_the_order_they_apply(authenticated_context: HttpContext) -> None:
    """`_params_matrix` applies scale then translation; the type publishes both as stated."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, scale=[2.0, 3.0], translation=[5.0, 7.0])

    published = await _registration(authenticated_context, slide)
    assert published["scale"] == [2.0, 3.0]
    assert published["translation"] == [5.0, 7.0]
    assert published["affine"] == []
    assert published["invariance"] == "AFFINE", "an anisotropic scale, so the weakest of the two"

    assert await sync_to_async(_sub_matrix)(edge) == [[2.0, 0.0, 5.0], [0.0, 3.0, 7.0], [0.0, 0.0, 1.0]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_wins_over_scale_and_translation(authenticated_context: HttpContext) -> None:
    """`_params_matrix` ignores scale/translation when an affine is present, and the type says so."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, affine=LATTICE, scale=[2.0, 3.0])

    composed = await sync_to_async(_sub_matrix)(edge)
    assert composed[0][:3] == approx(LATTICE[0]), "the scale beside the affine is not read"
    assert composed[1][:3] == approx(LATTICE[1])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_by_dimension_with_no_map_is_the_identity_on_the_axes_it_names(authenticated_context: HttpContext) -> None:
    """An identity registration is one row with empty params: no child, nothing to read but the names."""
    dataset = await seed.create_array_dataset(authenticated_context, "Placed", axes=seed.YX_AXES, shapes=[[64, 64]])
    scene = await seed.create_scene(authenticated_context, "Scene")
    edge = await seed.register_into_scene(authenticated_context, scene, dataset)

    assert edge.kind == enums.TransformKind.BY_DIMENSION.value
    assert edge.params == {} and edge.steps == []
    assert await models.Transformation.objects.filter(output=scene.world).acount() == 1
    assert await sync_to_async(_sub_matrix)(edge) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert await sync_to_async(graph_logic.invariance_of)(edge) == "ISOMETRY"
    assert await sync_to_async(graph_logic.is_invertible)(edge) is True

    published = await _registration(authenticated_context, await sync_to_async(lambda: scene.world)())
    assert published["scale"] == [] and published["translation"] == [] and published["affine"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refining_a_by_dimension_writes_the_row_a_client_reads(authenticated_context: HttpContext) -> None:
    """A refinement moves the edge's own `version` and what the type publishes, with no copy to go stale.

    With the map projected onto child rows, refining through `updateTransformation` rewrote
    `params`, the server composed the refinement, and the children a client read had to be
    regenerated to agree -- or did not, and out-voted the refinement in every viewer.
    """
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, scale=[2.0, 3.0])

    result = await schema.execute(UPDATE, context_value=authenticated_context, variable_values={"input": {"id": str(edge.pk), "scale": [4.0, 6.0]}})
    assert not result.errors, result.errors
    assert result.data["updateTransformation"]["version"] == 2, "the history row is on the edge itself"
    assert result.data["updateTransformation"]["scale"] == [4.0, 6.0]
    assert (await _registration(authenticated_context, slide))["scale"] == [4.0, 6.0]
    assert (await sync_to_async(_sub_matrix)(await models.Transformation.objects.aget(pk=edge.pk)))[0][0] == 4.0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sequence_publishes_its_steps_and_a_step_may_name_a_field(authenticated_context: HttpContext) -> None:
    """A level edge's steps read back in order with their numbers; a FIELD step resolves its system."""
    dataset = await seed.create_array_dataset(authenticated_context, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32]])

    def build() -> tuple[models.Transformation, models.CoordinateSystem]:
        intrinsic = dataset.intrinsic_coordinate_system
        field = models.CoordinateSystem.objects.create(name="Warp field", organization=authenticated_context.request.organization)
        models.Axis.objects.create(coordinate_system=field, order=0, name="d", type=enums.AxisTypeChoices.DISPLACEMENT.value)
        atlas = models.CoordinateSystem.objects.create(name="Atlas", organization=authenticated_context.request.organization)
        for index, name in enumerate(["c", "y", "x"]):
            models.Axis.objects.create(coordinate_system=atlas, order=index, name=name, type=enums.AxisTypeChoices.CHANNEL.value if name == "c" else enums.AxisTypeChoices.SPACE.value)
        warped = models.Transformation.objects.create(
            kind=enums.TransformKindChoices.SEQUENCE.value,
            input=intrinsic,
            output=atlas,
            steps=[{"kind": "SCALE", "params": {"scale": [1.0, 2.0, 2.0]}}, {"kind": "FIELD", "params": {}, "field": field.pk}],
            organization=authenticated_context.request.organization,
        )
        return warped, atlas

    warped, atlas = await sync_to_async(build)()

    result = await schema.execute(
        """
        query Steps($id: ID!, $atlas: ID!) {
          arrayDataset(id: $id) { dataArrays { level toParent { kind ... on SequenceTransformation { steps { kind scale translation field { id } } } } } }
          coordinateSystem(id: $atlas) { registrations { kind invariance ... on SequenceTransformation { steps { kind scale field { id name } } } } }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(dataset.pk), "atlas": str(atlas.pk)},
    )
    assert not result.errors, result.errors
    level_one = next(level for level in result.data["arrayDataset"]["dataArrays"] if level["level"] == 1)["toParent"]
    assert level_one["kind"] == "SEQUENCE"
    assert [step["kind"] for step in level_one["steps"]] == ["SCALE", "TRANSLATION"]
    assert level_one["steps"][0]["scale"] == [1.0, 2.0, 2.0] and level_one["steps"][0]["translation"] == [] and level_one["steps"][0]["field"] is None
    assert level_one["steps"][1]["translation"] == [0.0, 0.5, 0.5], "the half-voxel offset of a 2x downsample"

    (registration,) = result.data["coordinateSystem"]["registrations"]
    assert [step["kind"] for step in registration["steps"]] == ["SCALE", "FIELD"]
    assert registration["steps"][1]["field"]["name"] == "Warp field"
    assert registration["invariance"] == "DIFFEOMORPHIC", "the weakest of the steps"
    assert await sync_to_async(graph_logic.is_reverse_traversable)(warped) is False, "a sequence is as invertible as its least invertible step"


def test_a_step_is_not_an_edge_in_the_schema() -> None:
    """Steps are read off their sequence; nothing publishes a step as a transformation."""
    sdl = schema.as_str()
    sequence = sdl[sdl.find("type SequenceTransformation ") : sdl.find("\n}", sdl.find("type SequenceTransformation "))]
    by_dimension = sdl[sdl.find("type ByDimensionTransformation ") : sdl.find("\n}", sdl.find("type ByDimensionTransformation "))]
    filters = sdl[sdl.find("input TransformationFilter ") : sdl.find("\n}", sdl.find("input TransformationFilter "))]
    assert "steps: [TransformationStep!]!" in sequence
    assert "transformations" not in sequence and "transformations" not in by_dimension
    assert "scale: [Float!]!" in by_dimension and "affine: [[Float!]!]!" in by_dimension, "shaped as the single kinds' fields are, so one fragment reads every kind"
    assert "rootsOnly" not in filters
    assert "type TransformationStep" in sdl
