"""Tests for the round ROI kinds and the point set.

CIRCLE, SPHERE and ELLIPSOID are encoded as the two opposite corners of their
bounding box -- exactly what RECTANGLE and CUBE already are -- so a bounding box
is the vectors themselves rather than something a reader has to reconstruct from
a centre and a radius. That is the whole point of the encoding: the annotation
write path (``compute_intrinsic_bbox``) is kind-blind, so a corner-encoded kind
gets a correct ``intrinsicBbox`` and correct marquee bounds for free, while a
centre+radius kind would get a box around the centre point and the radius vector.

MULTI_POINT is the other axis: not a new box shape, a new *arity*. Its vectors
are unconnected points, which POINT (exactly one) and PATH (connected) both
already say something different about.
"""

import pytest
from asgiref.sync import sync_to_async
from pytest import approx

from core import enums, models
from kante.context import HttpContext
from mikro_server.schema import schema
from tests import seed


DRAW = """
mutation Create($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) {
    id
    kind
    vectors
    intrinsicBbox { min max }
  }
}
"""


# A legacy ROI vector is [c, t, z, y, x]. Centre (z=30, y=20, x=10), radius 5.
SPHERE_CORNERS = [
    [0, 0, 25, 15, 5],
    [0, 0, 35, 25, 15],
]


def test_the_new_kinds_reach_the_graphql_enum():
    """Both enums carry them: the Django one is what is stored, the strawberry one what is asked for.

    Named against ``AnnotationKind``, which is what backs ``Annotation.kind``. It used to assert
    against the removed ROI-kind pair -- enums that no field referenced and that were not in the
    SDL at all, so the membership check passed off ``AnnotationKind`` while naming the other one.
    """
    sdl = schema.as_str()

    for member in ("CIRCLE", "SPHERE", "ELLIPSOID", "MULTI_POINT"):
        assert member in sdl, f"{member} missing from the AnnotationKind enum"
        assert hasattr(enums.AnnotationKindChoices, member), f"{member} missing from AnnotationKindChoices"
        assert enums.AnnotationKind[member].value == enums.AnnotationKindChoices[member].value, "a stored value and a requested value must be the same string"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_drawn_sphere_gets_its_box_for_free(db, authenticated_context: HttpContext):
    """The kind-blind annotation write path already boxes a corner-encoded shape correctly.

    No branch in ``compute_intrinsic_bbox`` knows what a sphere is, and none needs
    to: the box is the half-open hull of the corners (a voxel at n covers
    [n - 0.5, n + 0.5)), identical to what the same corners give as a cube.
    """
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Spheres")

    # The drawing space mirrors the world's z/y/x, so annotation vectors are 3D.
    corners = [[25.0, 15.0, 5.0], [35.0, 25.0, 15.0]]

    result = await schema.execute(
        DRAW,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "SPHERE", "vectors": corners}},
    )
    assert not result.errors, result.errors
    sphere = result.data["createAnnotation"]

    assert sphere["kind"] == "SPHERE"
    assert sphere["intrinsicBbox"]["min"] == approx([24.5, 14.5, 4.5])
    assert sphere["intrinsicBbox"]["max"] == approx([35.5, 25.5, 15.5])

    cube = await schema.execute(
        DRAW,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "CUBE", "vectors": corners}},
    )
    assert not cube.errors, cube.errors
    assert cube.data["createAnnotation"]["intrinsicBbox"] == sphere["intrinsicBbox"], "same corners, same box: the round kind adds no second reading"

    stored = await models.Annotation.objects.aget(id=sphere["id"])
    assert stored.kind == enums.AnnotationKindChoices.SPHERE.value
    assert stored.bbox_cube is not None, "the GiST search copy is written like it is for every other kind"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_drawn_point_set_is_hulled_not_connected(db, authenticated_context: HttpContext):
    """MULTI_POINT stores every click; the box spans them all."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Counts")

    points = [[0.0, 4.0, 1.0], [0.0, 2.0, 9.0], [0.0, 7.0, 5.0]]

    result = await schema.execute(
        DRAW,
        context_value=ctx,
        variable_values={"input": {"scene": str(scene.id), "kind": "MULTI_POINT", "vectors": points}},
    )
    assert not result.errors, result.errors
    drawn = result.data["createAnnotation"]

    assert drawn["vectors"] == points, "every point survives; a point set is one annotation, not three"
    assert drawn["intrinsicBbox"]["min"] == approx([-0.5, 1.5, 0.5])
    assert drawn["intrinsicBbox"]["max"] == approx([0.5, 7.5, 9.5])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_new_kinds_are_filterable(db, authenticated_context: HttpContext):
    """`AnnotationFilter.kind` derives its enum from `AnnotationKindChoices`, so it picks the kinds up for free."""
    ctx = authenticated_context
    scene = await seed.create_scene(ctx, "Filterable")
    corners = [[25.0, 15.0, 5.0], [35.0, 25.0, 15.0]]

    drawn = {}
    for kind in ("SPHERE", "CUBE"):
        result = await schema.execute(
            DRAW,
            context_value=ctx,
            variable_values={"input": {"scene": str(scene.id), "kind": kind, "vectors": corners}},
        )
        assert not result.errors, result.errors
        drawn[kind] = result.data["createAnnotation"]["id"]

    result = await schema.execute(
        "query List($filters: AnnotationFilter) { annotations(filters: $filters) { id } }",
        context_value=ctx,
        variable_values={"filters": {"kind": "SPHERE"}},
    )
    assert not result.errors, result.errors
    assert {r["id"] for r in result.data["annotations"]} == {drawn["SPHERE"]}