"""A pyramid level placed where its writer says, checked against what its shape allows.

The shapes do not determine the map: 36 -> 4 is a factor of 9 for a resize and of 8 for
trimmed halvings (36, 18, 9, 4: the four voxels cover the first 32), and a stride keeps voxel 0
where a block reduction centres its voxel half a voxel in. So a writer that knows states
`scale` and `translation` per level, and a level that states neither is read as a resize
(`tests/test_coordinates.py` holds that fallback).
"""

from unittest.mock import patch

import pytest
from arkitekt_datalayer.models import ZarrStore
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from pytest import approx
from strawberry.types import ExecutionResult

from core import enums, models
from core.logic import coords
from mikro_server.schema import schema

_SPACE = enums.AxisTypeChoices.SPACE.value
_ZYX = [coords.AxisSpec(name=name, type=_SPACE) for name in ("z", "y", "x")]
_CZYX = [coords.AxisSpec(name="c", type=enums.AxisTypeChoices.CHANNEL.value), *_ZYX]


# --- the pure check ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape_0", "shape_level", "scale", "translation"),
    [
        # Trimmed halvings (a block mean or max that drops an odd remainder): 2**k, centred.
        ([36, 64, 64], [4, 8, 8], [8.0, 8.0, 8.0], [3.5, 3.5, 3.5]),
        # A chained stride (`[::2]`) ceils: 33 -> 17 -> 9 -> 5 at 8, voxel 0 kept.
        ([33, 64, 64], [5, 8, 8], [8.0, 8.0, 8.0], [0.0, 0.0, 0.0]),
        # A resize that rounds: 33 -> 17 at 33/17.
        (
            [33, 64, 64],
            [17, 32, 32],
            [33 / 17, 2.0, 2.0],
            [(33 / 17 - 1) / 2, 0.5, 0.5],
        ),
        # An axis left alone at its own level: scale 1, no offset.
        ([36, 64, 64], [36, 32, 32], [1.0, 2.0, 2.0], [0.0, 0.5, 0.5]),
    ],
)
def test_a_stated_level_the_shapes_allow_is_taken_as_stated(
    shape_0: list, shape_level: list, scale: list, translation: list
) -> None:
    assert coords.stated_level_transform(
        shape_0, shape_level, _ZYX, scale, translation
    ) == (scale, translation)


@pytest.mark.parametrize(
    ("shape_level", "scale", "translation", "message"),
    [
        # 36 / 4 = 9 voxels, and the level holds 4: a factor relative to the level above.
        (
            [4, 8, 8],
            [4.0, 8.0, 8.0],
            [1.5, 3.5, 3.5],
            "the level would hold 9 or 9 of them -- but it holds 4",
        ),
        ([4, 8, 8], [8.0, 8.0], [3.5, 3.5], "one scale and one translation per axis"),
        ([4, 8, 8], [8.0, 0.5, 8.0], [3.5, 0.0, 3.5], "never finer"),
        ([4, 8, 8], [8.0, 8.0, float("nan")], [3.5, 3.5, 3.5], "finite"),
        ([4, 8, 8], [8.0, 8.0, 8.0], [3.5, float("inf"), 3.5], "finite"),
    ],
)
def test_a_stated_level_the_shapes_refute_is_refused(
    shape_level: list, scale: list, translation: list, message: str
) -> None:
    with pytest.raises(
        ValueError, match=message.replace("(", r"\(").replace(")", r"\)")
    ):
        coords.stated_level_transform(
            [36, 64, 64], shape_level, _ZYX, scale, translation
        )


def test_a_categorical_axis_is_stated_as_untouched() -> None:
    """A channel keeps its positions: scale 1 and no offset, whatever the rest of the level does."""
    assert (
        coords.stated_level_transform(
            [2, 36, 64, 64],
            [2, 4, 8, 8],
            _CZYX,
            [1.0, 8.0, 8.0, 8.0],
            [0.0, 3.5, 3.5, 3.5],
        )[0][0]
        == 1.0
    )
    with pytest.raises(ValueError, match="categorical"):
        coords.stated_level_transform(
            [2, 36, 64, 64],
            [2, 4, 8, 8],
            _CZYX,
            [1.0, 8.0, 8.0, 8.0],
            [0.5, 3.5, 3.5, 3.5],
        )


# --- through createArrayDataset ---------------------------------------------------------

CREATE = """
mutation Create($input: CreateArrayDatasetInput!) {
  createArrayDataset(input: $input) { id }
}
"""

_AXES = [{"name": name, "type": "SPACE"} for name in ("z", "y", "x")]


async def _store(ctx: HttpContext, key: str, shape: list[int]) -> ZarrStore:
    return await ZarrStore.objects.acreate(
        organization=ctx.request.organization,
        key=key,
        bucket="zarr",
        shape=shape,
        chunks=shape,
        version="3",
        dtype="uint8",
        dimension_names=["z", "y", "x"],
        populated=True,
    )


async def _create(
    ctx: HttpContext, name: str, scales: list[tuple[list[int], dict]]
) -> ExecutionResult:
    """A z/y/x dataset of 36x64x64 with one store per level; each level's extra input fields as given."""
    base = await _store(ctx, f"{name}-0", [36, 64, 64])
    levels = []
    for index, (shape, fields) in enumerate(scales, start=1):
        store = await _store(ctx, f"{name}-{index}", shape)
        levels.append({"level": index, "array": str(store.pk), **fields})
    # `fill_info` would read the shape back from an S3 the unit tests do not have.
    with patch("arkitekt_datalayer.models.ZarrStore.fill_info", return_value=None):
        return await schema.execute(
            CREATE,
            context_value=ctx,
            variable_values={
                "input": {
                    "name": name,
                    "data": str(base.pk),
                    "axes": _AXES,
                    "scales": levels,
                }
            },
        )


def _level_map(dataset_id: str, level: int) -> tuple[list[float], list[float]]:
    """The stored edge of one level, read back as (scale, translation)."""
    array = models.DataArray.objects.get(dataset_id=dataset_id, level=level)
    edge = array.to_parent
    if edge.kind == enums.TransformKindChoices.SCALE.value:
        return edge.params["scale"], [0.0] * len(edge.params["scale"])
    steps = dict(edge.step_pairs)
    return steps["SCALE"]["scale"], steps["TRANSLATION"]["translation"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stated_trim_and_a_stated_stride_are_stored_as_stated(
    authenticated_context: HttpContext,
) -> None:
    """The two builders the shapes misread, side by side with a level that states nothing."""
    result = await _create(
        authenticated_context,
        "stated",
        [
            (
                [18, 32, 32],
                {
                    "scaleMethod": "NEAREST",
                    "scale": [2.0, 2.0, 2.0],
                    "translation": [0.0, 0.0, 0.0],
                },
            ),
            (
                [4, 8, 8],
                {
                    "scaleMethod": "AREA",
                    "scale": [8.0, 8.0, 8.0],
                    "translation": [3.5, 3.5, 3.5],
                },
            ),
            ([4, 8, 8], {}),
        ],
    )
    assert not result.errors, result.errors
    dataset_id = result.data["createArrayDataset"]["id"]

    stride = await sync_to_async(_level_map)(dataset_id, 1)
    assert stride == ([2.0, 2.0, 2.0], [0.0, 0.0, 0.0]), (
        "a stride keeps voxel 0: no offset"
    )
    assert (
        await sync_to_async(
            lambda: (
                models.DataArray.objects.get(
                    dataset_id=dataset_id, level=1
                ).to_parent.kind
            )
        )()
        == enums.TransformKindChoices.SCALE.value
    )

    trim = await sync_to_async(_level_map)(dataset_id, 2)
    assert trim == ([8.0, 8.0, 8.0], [3.5, 3.5, 3.5]), "trimmed halvings: 8, not 36/4"

    resize = await sync_to_async(_level_map)(dataset_id, 3)
    assert resize[0][0] == approx(9.0) and resize[1][0] == approx(4.0), (
        "stating nothing reads the level as a resize, as before"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_stated_level_is_refused_when_its_shape_disagrees_or_half_of_it_is_missing(
    authenticated_context: HttpContext,
) -> None:
    wrong = await _create(
        authenticated_context,
        "wrong",
        [([4, 8, 8], {"scale": [2.0, 8.0, 8.0], "translation": [0.5, 3.5, 3.5]})],
    )
    assert wrong.errors and "State the factor relative to level 0" in str(
        wrong.errors[0]
    ), str(wrong.errors and wrong.errors[0])

    half = await _create(
        authenticated_context, "half", [([4, 8, 8], {"scale": [8.0, 8.0, 8.0]})]
    )
    assert half.errors and "state `scale` and `translation` together" in str(
        half.errors[0]
    ), str(half.errors and half.errors[0])
    assert not await models.ArrayDataset.objects.filter(
        name__in=["wrong", "half"]
    ).aexists(), "a refused pyramid leaves no dataset behind"
