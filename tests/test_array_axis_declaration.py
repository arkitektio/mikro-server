"""The declared axes are checked against what the zarr itself says.

`ZarrStore.fill_info` has read `dimension_names` off the array since the field
existed (`datalayer/models.py:296`), and it is published in the SDL
(`datalayer/types.py:503`). Nothing ever compared it to the caller's `axes`. So a
`(z, y, x)` store declared `(x, y, z)` was accepted -- and the failure is not an
error: the render axes are derived from the *position* of the spatial axes, so it
renders transposed. Every one of the 46 array datasets in the live deployment
agrees with its store, which is what makes this safe to turn on; it is also why
nothing had ever noticed.

`axes` stays required regardless. `dimension_names` carries names only, and the
*type* is what the render derivation runs on -- so
mapping `{x, y, z} -> SPACE` here would be convention-guessing. The name is
redundant with the bytes; the type is not. Declare the type, get the name checked
for free.
"""

from unittest.mock import patch

import pytest
from datalayer.models import ZarrStore
from kante.context import HttpContext

from mikro_server.schema import schema


CREATE = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id intrinsicSystem { axes { name order } } }
}
"""

_ZYX = [
    {"name": "z", "type": "SPACE"},
    {"name": "y", "type": "SPACE"},
    {"name": "x", "type": "SPACE"},
]


async def _store(ctx: HttpContext, key: str, dimension_names: list | None, shape: list | None = None) -> ZarrStore:
    shape = shape or [8, 64, 64]
    return await ZarrStore.objects.acreate(
        organization=ctx.request.organization,
        key=key,
        bucket="zarr",
        shape=shape,
        chunks=shape,
        version="3",
        dtype="uint8",
        dimension_names=dimension_names,
        populated=True,
    )


async def _create(ctx: HttpContext, key: str, dimension_names: list | None, axes: list, shape: list | None = None):
    store = await _store(ctx, key, dimension_names, shape)
    # `fill_info` would overwrite `dimension_names` from an S3 the unit tests do not
    # have; the row is the point here, so it is patched out exactly as every other
    # array test does.
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        return await schema.execute(
            CREATE,
            context_value=ctx,
            variable_values={"input": {"name": key, "data": str(store.pk), "scales": [], "axes": axes}},
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_axes_that_name_the_stores_dimensions_are_accepted(authenticated_context: HttpContext):
    result = await _create(authenticated_context, "agrees", ["z", "y", "x"], _ZYX)

    assert not result.errors, result.errors
    axes = result.data["createArrayDataset"]["intrinsicSystem"]["axes"]
    assert [a["name"] for a in axes] == ["z", "y", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dimension_names",
    [["z", "c", "y", "x"], ["c", "z", "y", "x"], ["z", "y", "c", "x"]],
    ids=["z-first", "c-first", "c-between"],
)
async def test_an_acquisitions_own_dimension_order_is_accepted(authenticated_context: HttpContext, dimension_names: list):
    """The orderings the RFC-5 type rule used to refuse, which is most real stores.

    A channel axis after a spatial one, or between two of them, is how acquisitions are
    ordinarily written. The rule refused them to protect the render derivation, which does
    not consult it: the channel axis is found by type, and the spatial axes keep their
    relative order in all three declarations here, so all three render identically.

    The store's own `dimension_names` still has to agree -- that is the check that means
    something, and `test_a_transposed_declaration_is_refused` covers it.
    """
    axes = [{"name": name, "type": "CHANNEL" if name == "c" else "SPACE"} for name in dimension_names]
    result = await _create(
        authenticated_context,
        f"acquisition-{'-'.join(dimension_names)}",
        dimension_names,
        axes,
        shape=[8, 3, 64, 64] if dimension_names[1] == "c" else [8, 64, 3, 64] if dimension_names[2] == "c" else [3, 8, 64, 64],
    )

    assert not result.errors, str(result.errors and result.errors[0])
    stored = result.data["createArrayDataset"]["intrinsicSystem"]["axes"]
    assert [a["name"] for a in stored] == dimension_names, "stored in the store's order, not sorted into the RFC-5 one"
    assert [a["order"] for a in stored] == list(range(len(dimension_names))), "order is the index into the shape"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_transposed_declaration_is_refused(authenticated_context: HttpContext):
    """The whole point: this used to be accepted and render the wrong picture.

    `(z, y, x)` in the store, `(x, y, z)` declared. Both are well-formed -- all three
    axes are SPACE, so no ordering rule separates them -- and `resolve_render_axes`
    then takes the last spatial axis as x, so the dataset renders with z and x
    swapped. Nothing raises, at any point, without this check.
    """
    result = await _create(
        authenticated_context,
        "transposed",
        ["z", "y", "x"],
        [
            {"name": "x", "type": "SPACE"},
            {"name": "y", "type": "SPACE"},
            {"name": "z", "type": "SPACE"},
        ],
    )

    assert result.errors, "a transposed declaration must not be accepted"
    message = str(result.errors[0])
    assert "dimension 0 is 'z' in the store and was declared 'x'" in message
    assert "dimension 2 is 'x' in the store and was declared 'z'" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_store_that_names_no_dimensions_is_not_refused(authenticated_context: HttpContext):
    """zarr v2, or an array written before the field existed.

    The check is on what the bytes say, and these bytes say nothing. Refusing here
    would make a schema addition retroactively invalidate every store predating it --
    and 6 of the 173 zarr stores in the live deployment are exactly that.
    """
    result = await _create(authenticated_context, "unnamed-store", None, _ZYX)

    assert not result.errors, result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_null_dimension_name_is_skipped_not_compared(authenticated_context: HttpContext):
    """zarr v3 permits a null per dimension -- the store declining to name that one.

    `ZarrMetadata.dimension_names` is typed `list[str | None] | None`
    (`datalayer/base_models.py:274`) for exactly this. A null is an absence, not a
    disagreement, so the named dimensions are still checked around it.
    """
    result = await _create(authenticated_context, "partly-named", ["z", None, "x"], _ZYX)
    assert not result.errors, result.errors

    wrong = await _create(
        authenticated_context,
        "partly-named-wrong",
        ["z", None, "x"],
        [
            {"name": "z", "type": "SPACE"},
            {"name": "y", "type": "SPACE"},
            {"name": "q", "type": "SPACE"},
        ],
    )
    assert wrong.errors, "the dimensions that ARE named are still compared"
    assert "dimension 2 is 'x' in the store and was declared 'q'" in str(wrong.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_mismatch_is_prose_rather_than_an_assertion(authenticated_context: HttpContext):
    """It was a bare `assert` until 2026-08-20.

    Which vanishes under `-O` -- so the guard was off in exactly the deployment most
    likely to run that way -- and surfaced as `AssertionError` rather than as
    something a caller could act on.
    """
    result = await _create(
        authenticated_context,
        "rank-mismatch",
        ["z", "y", "x"],
        [{"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}],
    )

    assert result.errors
    # The *type*, not the text: a bare `assert` still carries its message, so
    # grepping the string would pass either way and this test would be checking
    # nothing. Verified by ablation -- put the `assert` back and this line fails.
    assert not isinstance(result.errors[0].original_error, AssertionError)
    assert "3 dimensions but 2 axes were declared" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_pyramid_level_is_checked_against_the_same_axes(authenticated_context: HttpContext):
    """A level is the same array at a coarser grid, so it has the same axes in the same order.

    A level whose zarr names its dimensions differently is the transposition bug one zoom
    down: the dataset renders correctly until the viewer crosses into that level, and then
    quietly does not. The per-level check was a second bare `assert`, on rank only.

    Measured before turning it on: all 103 pyramid-level stores in the live deployment agree
    with their dataset's axes, and none of them lacks `dimension_names`.
    """
    base = await _store(authenticated_context, "pyramid-base", ["z", "y", "x"])
    level = await ZarrStore.objects.acreate(
        organization=authenticated_context.request.organization,
        key="pyramid-level-1",
        bucket="zarr",
        shape=[4, 32, 32],
        chunks=[4, 32, 32],
        version="3",
        dtype="uint8",
        dimension_names=["z", "x", "y"],
        populated=True,
    )

    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            CREATE,
            context_value=authenticated_context,
            variable_values={
                "input": {
                    "name": "pyramid",
                    "data": str(base.pk),
                    "axes": _ZYX,
                    "scales": [{"level": 1, "array": str(level.pk)}],
                }
            },
        )

    assert result.errors, "a transposed pyramid level must not be accepted"
    message = str(result.errors[0])
    assert "Pyramid level 1" in message, "the level is named, since level 0 was fine"
    assert "dimension 1 is 'x' in the store and was declared 'y'" in message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_pyramid_level_that_agrees_is_accepted(authenticated_context: HttpContext):
    """The same shape, passing -- so the test above is failing on the names, not the pyramid."""
    base = await _store(authenticated_context, "good-base", ["z", "y", "x"])
    level = await ZarrStore.objects.acreate(
        organization=authenticated_context.request.organization,
        key="good-level-1",
        bucket="zarr",
        shape=[4, 32, 32],
        chunks=[4, 32, 32],
        version="3",
        dtype="uint8",
        dimension_names=["z", "y", "x"],
        populated=True,
    )

    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            CREATE,
            context_value=authenticated_context,
            variable_values={
                "input": {
                    "name": "good-pyramid",
                    "data": str(base.pk),
                    "axes": _ZYX,
                    "scales": [{"level": 1, "array": str(level.pk)}],
                }
            },
        )

    assert not result.errors, result.errors
