"""A composite edge is one row: a SEQUENCE carries its steps, a BY_DIMENSION its parts.

Both used to be trees of `Transformation` rows. A SEQUENCE's scale and translation were child
rows with no endpoints, with ids a client could refine one at a time -- writing the history on
the child, so the wrapper's `version` never moved -- and a BY_DIMENSION's map was stored in its
own `params` *and* projected as children so a client could read the numbers at all. Measured on
the Visium HD ingest: a 412x400 bin lattice registered onto an H&E slide by a fitted affine
(pitch 58.43 px, rotation 0.38 deg) should span 24 073 x 23 372 px; it drew 412 x 400 px at the
origin, because every client-side composer folded over an empty child list.

Now the numbers are where the type publishes them -- `steps` on a sequence, `parts` on a
by-dimension, each part one kind with its own axes -- and a refinement writes the row a client
reads.
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
      ... on ByDimensionTransformation { parts { kind inputAxes outputAxes scale translation affine } }
    }
  }
}
"""

UPDATE = """
mutation Update($input: UpdateTransformationInput!) {
  updateTransformation(input: $input) { id version ... on ByDimensionTransformation { parts { scale } } }
}
"""


def _creation(ctx: HttpContext) -> CreationContext:
    return CreationContext(user=ctx.request._user, organization=ctx.request._organization, membership=ctx.request._membership, task=None)


async def _systems(ctx: HttpContext) -> tuple[models.CoordinateSystem, models.CoordinateSystem]:
    """Two array datasets' intrinsic systems: a (row, col) lattice and a (c, y, x) slide."""
    lattice = await seed.create_array_dataset(ctx, "Lattice", axes=[axis("row", enums.AxisType.SPACE), axis("col", enums.AxisType.SPACE)], shapes=[[412, 400]])
    slide = await seed.create_array_dataset(ctx, "Slide", shapes=[[3, 21943, 23618]])
    return (await sync_to_async(lambda: lattice.coordinate_system)(), await sync_to_async(lambda: slide.coordinate_system)())


def _edge(ctx: HttpContext, input_system: models.CoordinateSystem, output_system: models.CoordinateSystem, kind: str, **params: object) -> models.Transformation:
    """One part, (row, col) onto (y, x), of ``kind`` with ``params``."""
    return graph_logic.build_registration_edge(
        input_system=input_system,
        output_system=output_system,
        kind=enums.TransformKind.BY_DIMENSION.value,
        parts=[{"kind": kind, "input_axes": ["row", "col"], "output_axes": ["y", "x"], **params}],
        ctx=_creation(ctx),
    )


async def _registration(ctx: HttpContext, system: models.CoordinateSystem) -> dict:
    result = await schema.execute(REGISTRATIONS, context_value=ctx, variable_values={"id": str(system.pk)})
    assert not result.errors, result.errors
    (edge,) = result.data["coordinateSystem"]["registrations"]
    return edge


def _map(edge: models.Transformation) -> list[list[float]]:
    """The edge composed, rows in its output system's order over its input system's axes plus the offset."""
    step = graph_logic._edge_step(edge)
    matrix, _rows = coords_logic.forms_to_matrix(coords_logic.compose_forms([step], step.input_axes), step.output_axes)
    return matrix


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_reaches_a_client_off_the_edge_itself(authenticated_context: HttpContext) -> None:
    """The matrix is on the row, in the part that publishes it, verbatim -- and nothing else is written."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, "AFFINE", affine=LATTICE)

    (part,) = (await _registration(authenticated_context, slide))["parts"]
    # Verbatim, not multiplied into something equivalent: a client rendering the reflection
    # as a rotation is the failure this exists to stop, and rounding it would hide that.
    assert part["kind"] == "AFFINE" and part["affine"] == LATTICE
    assert part["scale"] == [] and part["translation"] == []
    assert edge.steps == [] and edge.params == {}, "a by-dimension's map is its parts"
    assert await models.Transformation.objects.acount() == 1, "one edge, one row"

    composed = await sync_to_async(_map)(edge)
    assert composed[0] == approx(LATTICE[0])
    assert composed[1] == approx(LATTICE[1])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_scale_and_an_offset_are_one_affine_part_read_by_its_diagonal(authenticated_context: HttpContext) -> None:
    """One kind per part, so a pixel size beside a stage offset is a diagonal AFFINE -- and reads what its scale reads."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, "AFFINE", affine=[[2.0, 0.0, 5.0], [0.0, 3.0, 7.0]])

    published = await _registration(authenticated_context, slide)
    assert published["parts"][0]["affine"] == [[2.0, 0.0, 5.0], [0.0, 3.0, 7.0]]
    assert published["invariance"] == "AFFINE", "an anisotropic scale"
    assert await sync_to_async(_map)(edge) == [[2.0, 0.0, 5.0], [0.0, 3.0, 7.0]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_part_refuses_the_parameters_of_another_kind(authenticated_context: HttpContext) -> None:
    """An AFFINE part with a scale beside it is not a part whose scale nobody reads: it is refused."""
    lattice, slide = await _systems(authenticated_context)
    with pytest.raises(ValueError, match="does not read `scale`"):
        await sync_to_async(_edge)(authenticated_context, lattice, slide, "AFFINE", affine=LATTICE, scale=[2.0, 3.0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_identity_registration_is_one_identity_part(authenticated_context: HttpContext) -> None:
    """An identity registration is one row with one IDENTITY part: the axes it names, said twice."""
    dataset = await seed.create_array_dataset(authenticated_context, "Placed", axes=seed.YX_AXES, shapes=[[64, 64]])
    scene = await seed.create_scene(authenticated_context, "Scene")
    edge = await seed.register_into_scene(authenticated_context, scene, dataset)

    assert edge.kind == enums.TransformKind.BY_DIMENSION.value
    assert edge.params == {} and edge.steps == []
    assert [part["kind"] for part in edge.parts] == ["IDENTITY"]
    assert await models.Transformation.objects.filter(output=scene.world).acount() == 1
    assert await sync_to_async(graph_logic.invariance_of)(edge) == "ISOMETRY"
    assert await sync_to_async(graph_logic.is_invertible)(edge) is True

    (part,) = (await _registration(authenticated_context, await sync_to_async(lambda: scene.world)()))["parts"]
    assert part["kind"] == "IDENTITY" and part["scale"] == [] and part["translation"] == [] and part["affine"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_refining_a_by_dimension_writes_the_row_a_client_reads(authenticated_context: HttpContext) -> None:
    """A refinement names a part, moves the edge's own `version` and what the type publishes, with no copy to go stale."""
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(authenticated_context, lattice, slide, "SCALE", scale=[2.0, 3.0])

    result = await schema.execute(UPDATE, context_value=authenticated_context, variable_values={"input": {"id": str(edge.pk), "part": 0, "scale": [4.0, 6.0]}})
    assert not result.errors, result.errors
    assert result.data["updateTransformation"]["version"] == 2, "the history row is on the edge itself"
    assert result.data["updateTransformation"]["parts"][0]["scale"] == [4.0, 6.0]
    assert (await _registration(authenticated_context, slide))["parts"][0]["scale"] == [4.0, 6.0]
    assert (await sync_to_async(_map)(await models.Transformation.objects.aget(pk=edge.pk)))[0][0] == 4.0

    unnamed = await schema.execute(UPDATE, context_value=authenticated_context, variable_values={"input": {"id": str(edge.pk), "scale": [1.0, 1.0]}})
    assert unnamed.errors and "name the part" in str(unnamed.errors[0]), unnamed.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sequence_publishes_its_steps(authenticated_context: HttpContext) -> None:
    """A level edge's steps read back in order with their numbers."""
    dataset = await seed.create_array_dataset(authenticated_context, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32]])

    result = await schema.execute(
        """
        query Steps($id: ID!) {
          arrayDataset(id: $id) { dataArrays { level toParent { kind ... on SequenceTransformation { steps { kind scale translation } } } } }
        }
        """,
        context_value=authenticated_context,
        variable_values={"id": str(dataset.pk)},
    )
    assert not result.errors, result.errors
    level_one = next(level for level in result.data["arrayDataset"]["dataArrays"] if level["level"] == 1)["toParent"]
    assert level_one["kind"] == "SEQUENCE"
    assert [step["kind"] for step in level_one["steps"]] == ["SCALE", "TRANSLATION"]
    assert level_one["steps"][0]["scale"] == [1.0, 2.0, 2.0] and level_one["steps"][0]["translation"] == []
    assert level_one["steps"][1]["translation"] == [0.0, 0.5, 0.5], "the half-voxel offset of a 2x downsample"


def test_a_step_is_not_an_edge_in_the_schema() -> None:
    """Steps and parts are read off their edge; nothing publishes either as a transformation."""
    sdl = schema.as_str()
    sequence = sdl[sdl.find("type SequenceTransformation ") : sdl.find("\n}", sdl.find("type SequenceTransformation "))]
    by_dimension = sdl[sdl.find("type ByDimensionTransformation ") : sdl.find("\n}", sdl.find("type ByDimensionTransformation "))]
    part = sdl[sdl.find("type TransformationPart ") : sdl.find("\n}", sdl.find("type TransformationPart "))]
    filters = sdl[sdl.find("input TransformationFilter ") : sdl.find("\n}", sdl.find("input TransformationFilter "))]
    assert "steps: [TransformationStep!]!" in sequence
    assert "parts: [TransformationPart!]!" in by_dimension
    assert "transformations" not in sequence and "transformations" not in by_dimension
    assert "scale: [Float!]!" in part and "affine: [[Float!]!]!" in part, "shaped as the single kinds' fields are, so one fragment reads every kind"
    assert "rootsOnly" not in filters
    assert "type TransformationStep" in sdl
