"""A BY_DIMENSION publishes its own map as child rows, so a client can read it at all.

`ByDimensionTransformation` exposes ``transformations`` and nothing else -- no ``affine``, no
``scale``, no ``translation`` -- while `AffineTransformation` beside it resolves the very same
``params['affine']``. So a BY_DIMENSION written as one childless row had its matrix stored, used
server-side, and **unreadable by any client**. Every consumer that composes a path client-side
folds over the empty child list and comes back with an identity: in the viewer that is
`transformGraph.ts` for placement and `axisPath.ts` for attribute probes, and both fail silently.

Measured on the Visium HD ingest, which is what filed it as item 9 of MIKRO_BACKEND_PROPOSALS.md:
a 412x400 bin lattice registered onto an H&E slide by a fitted affine (pitch 58.43 px, rotation
0.38 deg, sub-pixel residual) should span 24 073 x 23 372 px. It drew 412 x 400 px at the origin.

The fix is a projection, and the invariant that makes it safe is that it is **only** a projection:
`_sub_matrix` reads a params-carried map in preference to children, so what the server composes is
byte-identical whether the rows are there or not, and a refinement through `updateTransformation`
cannot be out-voted by a stale copy of itself.
"""

import pytest
from asgiref.sync import sync_to_async
from pytest import approx

from core import enums, models
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic
from tests import seed
from tests.seed import axis


#: The real 016um Visium HD lattice fit: rows (y, x), columns (row, col, 1). Its linear part is
#: symmetric and its determinant negative -- a rotation with a reflection -- so a transposed or
#: sign-dropped copy of it still looks plausible. That is exactly why the numbers have to travel.
LATTICE = [
    [-58.4281240, -0.389111, 24102.7386],
    [-0.3888420, 58.4299340, 257.103715],
]


async def _systems(ctx):
    """Two array datasets' intrinsic systems: a (row, col) lattice and a (c, y, x) slide."""
    lattice = await seed.create_array_dataset(
        ctx,
        "Lattice",
        axes=[axis("row", enums.AxisType.SPACE), axis("col", enums.AxisType.SPACE)],
        shapes=[[412, 400]],
    )
    slide = await seed.create_array_dataset(ctx, "Slide", shapes=[[3, 21943, 23618]])
    return (
        await sync_to_async(lambda: lattice.coordinate_system)(),
        await sync_to_async(lambda: slide.coordinate_system)(),
    )


def _edge(ctx, input_system, output_system, **params):
    return graph_logic.build_registration_edge(
        input_system=input_system,
        output_system=output_system,
        kind=enums.TransformKind.BY_DIMENSION.value,
        input_axes=["row", "col"],
        output_axes=["y", "x"],
        ctx=ctx,
        **params,
    )


def _children(edge):
    return [(child.kind, child.params) for child in edge.children.order_by("order")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_reaches_a_client_as_a_child(db, authenticated_context):
    """The matrix is on a child row, in a type that publishes it."""
    ctx = await sync_to_async(_creation_context)(authenticated_context)
    lattice, slide = await _systems(authenticated_context)

    edge = await sync_to_async(_edge)(ctx, lattice, slide, affine=LATTICE)

    children = await sync_to_async(_children)(edge)
    assert children == [(enums.TransformKind.AFFINE.value, {"affine": LATTICE})]
    # And verbatim, not multiplied into something equivalent: a client rendering the reflection
    # as a rotation is the failure this exists to stop, and rounding it would hide that.
    assert edge.params["affine"] == LATTICE


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_projection_does_not_change_what_the_server_composes(db, authenticated_context):
    """A projected edge and a childless one compose to the same matrix.

    The whole safety argument: if these two ever disagree, the projection has become a second
    source of truth and every placement is a coin toss between them.
    """
    ctx = await sync_to_async(_creation_context)(authenticated_context)
    lattice, slide = await _systems(authenticated_context)
    edge = await sync_to_async(_edge)(ctx, lattice, slide, affine=LATTICE)

    def _both():
        step = coords_logic.AxedStep(
            kind=enums.TransformKind.BY_DIMENSION.value,
            params=edge.params,
            input_axes=["row", "col"],
            output_axes=["y", "x"],
            acts_on_input=["row", "col"],
            acts_on_output=["y", "x"],
            children=tuple((child.kind, child.params) for child in edge.children.order_by("order")),
        )
        childless = coords_logic.AxedStep(**{**step.__dict__, "children": ()})
        return coords_logic._sub_matrix(step), coords_logic._sub_matrix(childless)

    projected, childless = await sync_to_async(_both)()
    assert projected == childless
    # Pinned against the fit itself, so a change to either path has to face the real numbers.
    assert projected[0][:3] == approx(LATTICE[0])
    assert projected[1][:3] == approx(LATTICE[1])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scale_and_translation_project_in_the_order_they_apply(db, authenticated_context):
    """`_params_matrix` applies scale then translation, so the children say so in that order."""
    ctx = await sync_to_async(_creation_context)(authenticated_context)
    lattice, slide = await _systems(authenticated_context)

    edge = await sync_to_async(_edge)(ctx, lattice, slide, scale=[2.0, 3.0], translation=[5.0, 7.0])

    assert await sync_to_async(_children)(edge) == [
        (enums.TransformKind.SCALE.value, {"scale": [2.0, 3.0]}),
        (enums.TransformKind.TRANSLATION.value, {"translation": [5.0, 7.0]}),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_affine_wins_over_scale_and_translation(db, authenticated_context):
    """`_params_matrix` ignores scale/translation when an affine is present; so does this.

    Projecting all three would publish a composition the server never performs.
    """
    ctx = await sync_to_async(_creation_context)(authenticated_context)
    lattice, slide = await _systems(authenticated_context)

    edge = await sync_to_async(_edge)(ctx, lattice, slide, affine=LATTICE, scale=[2.0, 3.0])

    assert await sync_to_async(_children)(edge) == [(enums.TransformKind.AFFINE.value, {"affine": LATTICE})]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_wrapper_with_no_map_of_its_own_is_left_alone(db, authenticated_context):
    """`world_edge` writes `params={}` and one IDENTITY child, where the child IS the map.

    Regenerating children there would erase the edge's entire content, so a params-less
    BY_DIMENSION must not be touched.
    """
    ctx = await sync_to_async(_creation_context)(authenticated_context)
    lattice, slide = await _systems(authenticated_context)

    def _build():
        edge = models.Transformation.objects.create(
            kind=enums.TransformKind.BY_DIMENSION.value,
            input=lattice,
            output=slide,
            input_axes=["row", "col"],
            output_axes=["row", "col"],
            params={},
            creator=ctx.user,
            organization=ctx.organization,
        )
        models.Transformation.objects.create(
            kind=enums.TransformKind.IDENTITY.value, parent=edge, order=0, params={},
            creator=ctx.user, organization=ctx.organization,
        )
        graph_logic._project_by_dimension_children(edge, ctx)
        return _children(edge)

    assert await sync_to_async(_build)() == [(enums.TransformKind.IDENTITY.value, {})]


def _creation_context(ctx):
    from core.creation import CreationContext

    return CreationContext(
        user=ctx.request._user,
        organization=ctx.request._organization,
        membership=ctx.request._membership,
        task=None,
    )
