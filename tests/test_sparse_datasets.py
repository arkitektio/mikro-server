"""`createSparseDataset`: two identified axes, and one or both stored layouts.

A sparse dataset is the shape a colouring needs when a feature stops being a *schema* fact and
becomes a *data* one. Everything asserted here is a refusal that would otherwise be silent:

* a store whose shape contradicts the declared axes places every lookup one position out and
  raises nothing;
* two stores indexing the same axis are one capability twice, with nothing to say which a
  reader should use;
* an axis identified by nothing is not a lax dataset -- it is one no source can ever key,
  because `assert_edge_rank` can only account for an axis the edge supplies or the target
  identifies.

The load-bearing test is :func:`test_the_keyed_axis_is_produced_and_the_referenced_one_is_not`:
a mask supplies one id, and the other axis is accounted for without it.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from datalayer.models import sparse_layout_path
from mikro_server.schema import schema
from tests import seed

CREATE = """
mutation Create($input: CreateSparseDatasetInput!) {
  createSparseDataset(input: $input) {
    id
    name
    axisNames
    shape
    indexableAxes
    arrays { indexedAxis indexedAxisName path store { id spec shape layouts { path encoding indexedAxis indexOrder rangeReadable } } }
    axisReferences { axis references { id name } }
    coordinateSystem { id axes { name type } }
  }
}
"""

PLANS = """
query Plans($system: ID!) {
  attributePlans(system: $system) { hops { table { name } } sample { produces passthrough } }
}
"""

#: A mask whose pixels are object ids. `(y, x)` in, one id out.
YX_AXES = [seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]

#: The declared axes of the matrix, in the order its stores' `shape` is written.
def _axes(mask=None, features=None, *, keyed: str = "object", table_axis: str = "feature") -> list[dict]:
    """The two axes, each carrying what identifies it.

    A builder rather than a constant now: identification lives on the axis, so the axes cannot be
    written without the things that identify them -- which is the point of the shape.
    """
    identifications = {
        keyed: {"kind": "DATASET", "dataset": str(mask.pk)} if mask is not None else None,
        table_axis: {"kind": "TABLE", "table": features} if features is not None else None,
    }
    return [{"name": name, "identifiedBy": [identifications[name]] if identifications[name] else []} for name in ("feature", "object")]

#: 40 features x 12 objects. Small, and the two extents differ so a transposed shape is caught.
SHAPE = [40, 12]


async def _mask(ctx: HttpContext, name: str = "cell labels"):
    """A label mask whose level-0 array has a zarr store, so a plan has something to name.

    `resolve_field_store` refuses a storeless array outright -- a plan is instructions for a
    worker, and one that cannot say where to sample is not instructions.
    """
    dataset = await seed.create_array_dataset(ctx, name, axes=YX_AXES, shapes=[[64, 64]])

    def attach() -> None:
        store = models.ZarrStore.objects.create(path=f"s3://zarr/{name}", bucket="zarr", key=name.replace(" ", "-"), organization=ctx.request.organization)
        array = dataset.data_arrays.get(level=0)
        array.store = store
        array.save()

    await sync_to_async(attach)()
    return dataset


def _layout(axis: int, rank: int = 2, nnz: int = 96) -> dict:
    """One entry of a store's `layouts`, as `finishSparseUpload` would have recorded it."""
    return seed.sparse_layout(axis, rank=rank, nnz=nnz)


async def _store(ctx: HttpContext, key: str, axes: tuple[int, ...] = (0,), shape: list[int] | None = None) -> models.SparseStore:
    """A finished sparse store holding a layout per axis in ``axes``. See `seed.create_sparse_store`."""
    return await seed.create_sparse_store(ctx, key, axes=axes, shape=list(shape if shape is not None else SHAPE))


async def _features_table(ctx: HttpContext, name: str = "features") -> str:
    """A table one feature position identifies a row of: keyed by a single INDEX column."""
    parquet = await sync_to_async(models.ParquetStore.objects.create)(path=f"s3://parquet/{name}", bucket="parquet", key=name, organization=ctx.request.organization, populated=True, columns=[{"name": "feature_id", "type": "BIGINT", "nullable": True}, {"name": "symbol", "type": "VARCHAR", "nullable": True}])
    result = await schema.execute(
        "mutation Create($input: CreateTableDatasetInput!) { createTableDataset(input: $input) { id } }",
        context_value=ctx,
        variable_values={
            "input": {
                "name": name,
                "data": str(parquet.pk),
                "columns": [
                    {"name": "feature_id", "dtype": "BIGINT", "axisType": "INDEX"},
                    {"name": "symbol", "dtype": "VARCHAR", "role": "LABEL"},
                ],
            }
        },
    )
    assert not result.errors, result.errors
    return result.data["createTableDataset"]["id"]


async def _create(ctx: HttpContext, name: str, **extra: object) -> object:
    return await schema.execute(CREATE, context_value=ctx, variable_values={"input": {"name": name, **extra}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_both_layouts_land_as_two_arrays_of_one_dataset(authenticated_context: HttpContext):
    """One matrix, two stores, and the axis each indexes derived from its own encoding."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "matrix", axes=(0, 1))

    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert not result.errors, result.errors
    dataset = result.data["createSparseDataset"]

    assert dataset["axisNames"] == ["feature", "object"]
    assert dataset["shape"] == SHAPE, "read off the stores, never declared"
    assert [axis["type"] for axis in dataset["coordinateSystem"]["axes"]] == ["INDEX", "INDEX"]

    arrays = {array["indexedAxisName"]: array["path"] for array in dataset["arrays"]}
    assert arrays == {"feature": "layouts/axis0", "object": "layouts/axis1"}, "each layout is a child of the one store"
    assert len({array["store"]["id"] for array in dataset["arrays"]}) == 1, "one matrix is one upload"
    assert sorted(dataset["indexableAxes"]) == ["feature", "object"], "both questions answerable in one read"

    assert [(entry["axis"], entry["references"]["name"]) for entry in dataset["axisReferences"]] == [("feature", "features")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_layout_is_legal_and_offers_one_capability(authenticated_context: HttpContext):
    """A dataset with a single store is not half-built: it answers one question, and says so."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    by_feature = await _store(authenticated_context, "by-feature", axes=(0,))

    result = await _create(
        authenticated_context,
        "expression",
        store=str(by_feature.pk),
        axes=_axes(mask, features),
    )
    assert not result.errors, result.errors
    assert result.data["createSparseDataset"]["indexableAxes"] == ["feature"], "one store, one capability"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_keyed_axis_is_produced_and_the_referenced_one_is_not(authenticated_context: HttpContext):
    """The rule this whole shape rests on: a mask supplies one id, and that is enough.

    `assert_edge_rank` accounts for every axis of a FIELD's target -- by the edge, or by the
    target's own identification. `feature` is identified by `references`, so the edge produces
    only `object`, and the edge is written rather than refused.
    """
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "by-feature", axes=(0,))

    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert not result.errors, result.errors

    def edges() -> list[models.Transformation]:
        system = models.CoordinateSystem.objects.get(pk=result.data["createSparseDataset"]["coordinateSystem"]["id"])
        return list(models.Transformation.objects.filter(output=system, kind=enums.TransformKind.FIELD.value))

    field_edges = await sync_to_async(edges)()
    assert len(field_edges) == 1, "one source, one FIELD edge"
    assert field_edges[0].output_axes == ["object"], "the mask supplies the object id and nothing about a feature"
    assert sorted(field_edges[0].input_axes) == ["x", "y"], "and it consumes the pixel grid to do it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_identified_by_nothing_is_refused(authenticated_context: HttpContext):
    """`identifiedBy` is a list now, so the empty case is expressible and comes back as prose.

    It was a singular required field, and "identified by nothing" was a document GraphQL would
    not accept -- a stronger guarantee, and the one thing the list form gives up. It is worth it:
    a singular field cannot say "keyed by a nucleus mask *and* a cell mask", which
    `write_key_edges` has always supported and which is an ordinary case. One line buys it back.
    """
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "by-feature", axes=(0,))

    axes = _axes(mask, features)
    axes[0]["identifiedBy"] = []
    result = await _create(authenticated_context, "expression", store=str(store.pk), axes=axes)

    assert result.errors
    message = str(result.errors[0])
    assert "empty `identifiedBy`" in message
    assert "no source could ever key" in message
    assert not await sync_to_async(models.SparseDataset.objects.filter(name="expression").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_matrix_nothing_keys_is_reachable_only_through_a_hop(authenticated_context: HttpContext):
    """Every axis identified by a table: no FIELD edge, no colouring -- and legal, because a plan can hop in.

    This used to be refused as "quietly useless": nothing stands on such a matrix, so no layer
    reaches it. That reason lapsed when plans grew hops (`core/logic/join_walk.py`): a plan that
    lands in `features` walks *into* this matrix along the axis `features` identifies. What stays
    true is that it publishes no plan of its own -- there is no edge to publish one from.
    """
    features = await _features_table(authenticated_context, "features")
    others = await _features_table(authenticated_context, "others")
    store = await _store(authenticated_context, "by-feature", axes=(0,))

    result = await _create(
        authenticated_context,
        "membership",
        store=str(store.pk),
        axes=[
            {"name": "feature", "identifiedBy": [{"kind": "TABLE", "table": features}]},
            {"name": "object", "identifiedBy": [{"kind": "TABLE", "table": others}]},
        ],
    )
    assert not result.errors, result.errors
    dataset = result.data["createSparseDataset"]
    assert sorted(reference["axis"] for reference in dataset["axisReferences"]) == ["feature", "object"]

    system = dataset["coordinateSystem"]["id"]
    edges = await sync_to_async(lambda: [(e.kind, e.input.name, e.output.name, e.input_axes, e.output_axes) for e in models.Transformation.objects.filter(output_id=system)])()
    assert not [e for e in edges if e[0] == "FIELD"], f"no source keys it, so no FIELD edge: {edges}"
    plans = await schema.execute(PLANS, context_value=authenticated_context, variable_values={"system": system})
    assert not plans.errors, plans.errors
    assert plans.data["attributePlans"] == [], "and so no plan of its own -- it is reached, never probed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_identified_two_ways_at_once_is_refused(authenticated_context: HttpContext):
    """The union reads one id field, chosen by `kind`, and rejects any other."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "by-feature", axes=(0,))

    axes = _axes(mask, features)
    axes[0]["identifiedBy"] = [{"kind": "TABLE", "table": features, "dataset": str(mask.pk)}]
    result = await _create(authenticated_context, "expression", store=str(store.pk), axes=axes)

    assert result.errors
    assert "dataset" in str(result.errors[0]), "name the field that does not belong to this kind"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_keyed_axis_the_source_cannot_supply_is_refused(authenticated_context: HttpContext):
    """The check the caller's statement buys: the derivation now has something to disagree with.

    Naming the axis is not naming the split -- `consumed` and the passthrough are still derived --
    but it lets the refusal say which axis the caller meant, instead of counting ids.
    """
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "by-feature", axes=(0,))

    # The mask spans (y, x), so an axis called `y` is one it *shares* with the matrix -- it passes
    # through by name rather than being supplied, and no source ever produces it. Naming an axis
    # the mask does not share would be fine whichever way round it is, which is worth knowing:
    # `feature` keyed by the mask and `object` by a table is not an error, only unusual.
    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=[
            {"name": "y", "identifiedBy": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
            {"name": "object", "identifiedBy": [{"kind": "TABLE", "table": features}]},
        ],
    )
    assert result.errors
    message = str(result.errors[0])
    assert "was declared to key 'y'" in message, "name the axis the caller meant"
    assert "passes through rather than being supplied" in message, "say why, not just that it is wrong"
    assert not await sync_to_async(models.SparseDataset.objects.filter(name="expression").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_stores_indexing_one_axis_are_refused(authenticated_context: HttpContext):
    """One capability twice, and nothing to say which a reader should use."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "one", axes=(0,))
    await sync_to_async(models.SparseStore.objects.filter(pk=store.pk).update)(layouts=[_layout(0), _layout(0)])

    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert result.errors
    assert "one capability twice" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_store_whose_shape_contradicts_the_axes_is_refused(authenticated_context: HttpContext):
    """The check only possible because the store read its own shape rather than being told it.

    Two *stores* disagreeing about the shape is no longer representable -- one matrix is one
    upload, so there is one declaration -- which is a simplification rather than a hole. What is
    still checkable, and still silent if missed, is a declaration whose rank does not match the
    bytes: every lookup would land one axis out.
    """
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "one", axes=(0,), shape=[*SHAPE, 2])

    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert result.errors
    assert "the same number of them" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unfinished_store_is_refused(authenticated_context: HttpContext):
    """An unfinished store knows nothing about itself, so a dataset over it would know nothing."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context)
    store = await _store(authenticated_context, "unfinished", axes=(0,))
    await sync_to_async(models.SparseStore.objects.filter(pk=store.pk).update)(populated=False, layouts=None, shape=None)

    result = await _create(
        authenticated_context,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert result.errors
    assert "finishSparseUpload" in str(result.errors[0]), "name the step that would have read it"


# `test_a_non_index_axis_is_refused` lived here. `SparseAxisInput` has no `type` field, so a
# CHANNEL axis is no longer something a caller can write and the refusal has nothing to catch --
# the check and the field that made it necessary were removed together.


CREATE_LABEL_LAYER = """
mutation Create($input: CreateLabelLayerInput!) {
  createLabelLayer(input: $input) {
    id
    labelRender {
      colorBys { kind table column dataset at { axis value } colormap min max label }
      activeColorBy
      filterBys { kind table column dataset at { axis value } min max values exclude label }
      activeFilterBys
    }
  }
}
"""


async def _placed_mask(ctx: HttpContext):
    """A mask with a lens and a scene it is registered into -- what a label layer needs."""
    mask = await _mask(ctx)
    lens = await seed.create_lens(ctx, mask)
    scene = await seed.create_scene(ctx, "cells")
    await seed.register_into_scene(ctx, scene, mask)
    return mask, lens, scene


async def _expression(ctx: HttpContext, mask, *, axes: tuple[int, ...] = (0, 1)) -> str:
    """A sparse dataset keyed by ``mask``, its feature axis identified by a table."""
    features = await _features_table(ctx)
    store = await _store(ctx, f"store-{'-'.join(str(axis) for axis in axes)}", axes=axes)
    result = await _create(
        ctx,
        "expression",
        store=str(store.pk),
        axes=_axes(mask, features),
    )
    assert not result.errors, result.errors
    return result.data["createSparseDataset"]["id"]


async def _colour(ctx, lens, scene, entry: dict) -> object:
    return await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=ctx,
        variable_values={"input": {"lens": str(lens.id), "scene": str(scene.id), "render": {"colorBys": [entry], "activeColorBy": 0}}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_label_layer_colours_by_one_slice_of_a_matrix(authenticated_context: HttpContext):
    """The point of the whole exercise: a feature colours the mask, and nothing was rasterised.

    The mask's pixels are object ids; the matrix is indexed by those same ids on one axis and
    by a feature table on the other. Naming a position along the feature axis is naming one
    slice -- a value per object -- which is exactly what a colouring is.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 7}], "colormap": "MAGMA", "min": 0.0, "max": 12.0, "label": "Feature 7"},
    )
    assert not result.errors, result.errors
    entry = result.data["createLabelLayer"]["labelRender"]["colorBys"][0]

    assert entry["kind"] == "SPARSE"
    assert entry["dataset"] == dataset
    assert entry["at"] == [{"axis": "feature", "value": 7}]
    assert entry["table"] is None and entry["column"] is None, "the other variant's fields are null, not empty strings"
    assert (entry["colormap"], entry["min"], entry["max"]) == ("MAGMA", 0.0, 12.0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_slice_the_store_would_have_to_scan_for_is_refused(authenticated_context: HttpContext):
    """The layout rule, made visible. This is why the model holds stores at all.

    With only the object-major layout, reading one feature means touching every object's run --
    a scan of the whole store rather than one contiguous range, measured at 1 777 ms against
    2.2 ms. A colouring the server knows would do that is one it refuses rather than publishes.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask, axes=(1,))

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 7}], "colormap": "MAGMA"},
    )
    assert result.errors
    message = str(result.errors[0])
    assert "holds no layout indexed on any of ['feature']" in message
    assert "scanning every byte" in message, "say what it would cost, not just that it is refused"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_position_along_the_keyed_axis_is_refused(authenticated_context: HttpContext):
    """Naming the object axis asks for one object's whole profile, which is a hover, not a colouring."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "object", "value": 3}], "colormap": "MAGMA"},
    )
    assert result.errors
    assert "one object's whole profile" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_out_of_range_position_is_refused(authenticated_context: HttpContext):
    """A position is a row of the table that axis references, not an id of its own."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": SHAPE[0]}], "colormap": "MAGMA"},
    )
    assert result.errors
    assert f"runs 0..{SHAPE[0] - 1}" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_colouring_is_measured_and_takes_no_qualitative_colormap(authenticated_context: HttpContext):
    """A slice is a value per object. Nothing stores categories sparsely -- the zeros would be one."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 1}], "colormap": "HUES"},
    )
    assert result.errors
    assert "qualitative" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_matrix_no_field_edge_reaches_is_refused(authenticated_context: HttpContext):
    """The ids that select a value have to be the ids this source supplies."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    other = await _mask(authenticated_context, "other labels")
    dataset = await _expression(authenticated_context, other)

    result = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 1}], "colormap": "MAGMA"},
    )
    assert result.errors
    assert "not reachable from this mask by a FIELD edge" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_slices_of_one_matrix_are_two_colourings(authenticated_context: HttpContext):
    """The duplicate check keys on the whole rendering, and a position is part of it.

    Before the variant's fields joined that key, two different features would have keyed
    identically and the second been refused as a copy of the first -- which is the whole
    feature failing on its second entry.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "lens": str(lens.id),
                "scene": str(scene.id),
                "render": {
                    "colorBys": [
                        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 3}], "colormap": "MAGMA", "label": "Feature 3"},
                        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 9}], "colormap": "MAGMA", "label": "Feature 9"},
                    ],
                    "activeColorBy": 1,
                },
            }
        },
    )
    assert not result.errors, result.errors
    render = result.data["createLabelLayer"]["labelRender"]
    assert [entry["at"][0]["value"] for entry in render["colorBys"]] == [3, 9]
    assert render["activeColorBy"] == 1, "and switching between them is an index, not a re-upload"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_matrix_a_picker_names_cannot_be_deleted(authenticated_context: HttpContext):
    """The PROTECT a JSON column cannot express as a foreign key.

    A colouring names its source by id inside `label_render`, so nothing cascades. Without this
    guard the delete would succeed and the entry survive -- looking valid until a renderer went
    looking for bytes that are gone, in a viewer, to whoever opened the scene next.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    coloured = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 2}], "colormap": "MAGMA"},
    )
    assert not coloured.errors, coloured.errors

    result = await schema.execute(
        "mutation Delete($input: DeleteSparseDatasetInput!) { deleteSparseDataset(input: $input) }",
        context_value=authenticated_context,
        variable_values={"input": {"id": dataset}},
    )
    assert result.errors, "a matrix a picker names must not be deletable"
    message = str(result.errors[0])
    assert "colour by a slice of it" in message
    assert "layer" in message and "scene" in message, "name what is holding it, not just that something is"
    assert await sync_to_async(models.SparseDataset.objects.filter(pk=dataset).exists)(), "the refusal left it in place"


OPTIONS = """
query Options($lens: ID!, $filters: ColumnOptionFilter) {
  labelColorByOptions(lens: $lens, filters: $filters) {
    control
    column { name }
    table { name }
    sparseDataset { id name }
    axes
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_picker_offers_the_sliceable_axis_not_every_position(authenticated_context: HttpContext):
    """One option per axis, and only an axis a stored layout indexes.

    Enumerating positions is what this whole design exists to avoid -- a matrix with 19 059
    features would be 19 059 options through GraphQL. The option names the slice-able axis; the
    client picks the position out of the table that axis references, which it already holds a
    grant for. That is the same line `core.logic.tables` draws about a picker wanting values.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await schema.execute(OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id), "filters": None})
    assert not result.errors, result.errors
    sparse = [option for option in result.data["labelColorByOptions"] if option["sparseDataset"] is not None]

    assert len(sparse) == 1, "one option per matrix, not one per position and not one per axis"
    assert sparse[0]["axes"] == ["feature"], "the axes it identifies itself -- not the one the mask's ids index"
    assert sparse[0]["sparseDataset"]["id"] == dataset
    assert sparse[0]["control"] == "MEASURE", "a slice is a value per object; there is nothing categorical about it"
    assert sparse[0]["table"] is None and sparse[0]["column"] is None, "an option is one half or the other, never both"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_axis_no_layout_indexes_is_not_offered(authenticated_context: HttpContext):
    """The invariant: everything offered is something the mutation accepts.

    With only the object-major layout, a colouring along `feature` is refused -- so offering it
    would be a picker proposing what the write path declines, which is the one failure the
    options query exists to prevent.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    await _expression(authenticated_context, mask, axes=(1,))

    result = await schema.execute(OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id), "filters": None})
    assert not result.errors, result.errors
    assert not [option for option in result.data["labelColorByOptions"] if option["sparseDataset"] is not None], (
        "an axis no stored layout indexes is a scan, and a scan is not offered"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_narrowing_by_role_excludes_the_sparse_half(authenticated_context: HttpContext):
    """A role is a property of a column, and a slice of a matrix has none.

    So filtering for one returns fewer options, never more -- asking for what an option does not
    have must not match all of them.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    await _expression(authenticated_context, mask)

    result = await schema.execute(
        OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id), "filters": {"roles": ["ATTRIBUTE"]}}
    )
    assert not result.errors, result.errors
    assert not [option for option in result.data["labelColorByOptions"] if option["sparseDataset"] is not None]

    searched = await schema.execute(
        OPTIONS, context_value=authenticated_context, variable_values={"lens": str(lens.id), "filters": {"search": "feature"}}
    )
    assert not searched.errors, searched.errors
    assert [option["axes"] for option in searched.data["labelColorByOptions"] if option["sparseDataset"]] == [["feature"]], (
        "searching matches the axis name and the matrix's own"
    )


SPARSE_PLANS = """
query Plans($system: ID!) {
  attributePlans(system: $system) {
    sample { consumes produces passthrough }
    hops {
      table { name }
      sparseDataset { id name }
      lookup { kind keyAxis keyHeld valueAxes sparseArray { path indexedAxis store { id } } keyColumns { axis } }
    }
  }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_matrix_publishes_a_plan_that_reads_one_slice(authenticated_context: HttpContext):
    """Hover, and the other half of why both layouts are stored.

    The id selects a *slice*, not a row: one read of `indptr` at the object, one of the range
    it names, and back comes every feature that object carries a value for. That is what "what
    is in this object" means for a matrix, and it is a different lookup kind rather than a
    `WHERE` with a hole in it.
    """
    mask = await _mask(authenticated_context)
    await _expression(authenticated_context, mask)
    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await schema.execute(SPARSE_PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not result.errors, result.errors
    plans = [plan for plan in result.data["attributePlans"] if plan["hops"][0]["sparseDataset"] is not None]
    assert len(plans) == 1, "one plan per matrix the ids index"
    plan = plans[0]

    assert plan["hops"][0]["table"] is None, "a plan lands in one or the other, never both"
    assert plan["sample"]["produces"] == ["object"], "the mask supplies the object id"
    assert plan["hops"][0]["lookup"]["kind"] == "SPARSE"
    assert plan["hops"][0]["lookup"]["keyHeld"] == "object", "held under the axis' own name: the sample produced it"
    assert plan["hops"][0]["lookup"]["keyColumns"] == [], "and no columns to bind"
    assert plan["hops"][0]["lookup"]["keyAxis"] == "object", "the id binds to the axis the store's indptr indexes"
    assert plan["hops"][0]["lookup"]["valueAxes"] == ["feature"], "and every position along the others comes back -- one axis at rank two"
    assert plan["hops"][0]["lookup"]["sparseArray"]["path"] == "layouts/axis1", "the object-major layout, which is the one that can answer this"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_plan_when_only_the_wrong_layout_is_stored(authenticated_context: HttpContext):
    """A plan for a scan is not a slow plan; it is one nobody should execute.

    With only the feature-major layout, reading one object means touching every feature's run.
    The dataset simply publishes no plan until the transposed layout is registered -- the same
    conclusion, and for the same reason, as the colouring that gets refused in the mirror case.
    """
    mask = await _mask(authenticated_context)
    await _expression(authenticated_context, mask, axes=(0,))
    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    result = await schema.execute(SPARSE_PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not result.errors, result.errors
    assert not [plan for plan in result.data["attributePlans"] if plan["hops"][0]["sparseDataset"] is not None]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_two_layouts_answer_the_two_questions(authenticated_context: HttpContext):
    """The whole argument for storing both, in one test.

    Feature-major answers the colouring ("every object's value for this feature"); object-major
    answers the plan ("every feature's value for this object"). Neither can answer the other's,
    so a dataset holding both offers both and a dataset holding one offers one.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)
    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()

    coloured = await _colour(
        authenticated_context, lens, scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 5}], "colormap": "MAGMA"},
    )
    assert not coloured.errors, coloured.errors

    plans = await schema.execute(SPARSE_PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not plans.errors, plans.errors
    plan = next(plan for plan in plans.data["attributePlans"] if plan["hops"][0]["sparseDataset"] is not None)

    assert plan["hops"][0]["lookup"]["sparseArray"]["path"] == "layouts/axis1", "hover reads the object-major layout"
    assert coloured.data["createLabelLayer"]["labelRender"]["colorBys"][0]["at"] == [{"axis": "feature", "value": 5}]


# --------------------------------------------------------------------------- #
# Rank three -- two axes is one case, not the definition
# --------------------------------------------------------------------------- #
#: 40 features x 12 objects x 3 timepoints. Three different extents, so an axis mix-up cannot
#: survive a shape check by coincidence.
CUBE_SHAPE = [40, 12, 3]
def _cube_axes(mask, features, timepoints) -> list[dict]:
    """Three axes, one keyed and two referenced. The rule does not change with rank."""
    return [
        {"name": "feature", "identifiedBy": [{"kind": "TABLE", "table": features}]},
        {"name": "object", "identifiedBy": [{"kind": "DATASET", "dataset": str(mask.pk)}]},
        {"name": "timepoint", "identifiedBy": [{"kind": "TABLE", "table": timepoints}]},
    ]


async def _cube_dataset(ctx: HttpContext, mask, features, timepoints, store) -> str:
    """A rank-three dataset over ``store``: one keyed axis and two referenced ones."""
    created = await schema.execute(
        CREATE,
        context_value=ctx,
        variable_values={"input": {"name": "expression over time", "axes": _cube_axes(mask, features, timepoints), "store": str(store.pk)}},
    )
    assert not created.errors, created.errors
    return created.data["createSparseDataset"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_three_matrix_is_a_sparse_dataset(authenticated_context: HttpContext):
    """A layout is one axis made contiguous, so an array of rank n has up to n of them.

    The store, the datalayer reader and this mutation are all written that way. A
    (feature, object, timepoint) matrix answers "this feature", "this object" and "this
    timepoint" in one contiguous read each, and costs one stored layout per question.

    The identification rule does not change and is what makes the shape hold together: the mask
    supplies the object id, and the other two axes are identified by the tables whose rows their
    positions are. Every axis accounted for exactly once, at any rank.
    """
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    store = await _store(authenticated_context, "cube", axes=(0, 1, 2), shape=CUBE_SHAPE)

    result = await schema.execute(
        CREATE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "expression over time",
                "axes": _cube_axes(mask, features, timepoints),
                "store": str(store.pk),
            }
        },
    )
    assert not result.errors, result.errors
    dataset = result.data["createSparseDataset"]

    assert dataset["axisNames"] == ["feature", "object", "timepoint"]
    assert dataset["shape"] == CUBE_SHAPE, "read off the store, never declared"
    assert sorted(dataset["indexableAxes"]) == ["feature", "object", "timepoint"], "all three questions answerable"
    assert {array["path"] for array in dataset["arrays"]} == {"layouts/axis0", "layouts/axis1", "layouts/axis2"}
    assert len({array["store"]["id"] for array in dataset["arrays"]}) == 1, "one matrix is one upload, at any rank"

    # Above rank two every layout is a csr_matrix over the raveled view, and `indexOrder` is what
    # makes a returned position meaningful -- it is not derivable from the bytes.
    layouts = {entry["indexedAxis"]: entry for entry in dataset["arrays"][0]["store"]["layouts"]}
    assert {entry["encoding"] for entry in layouts.values()} == {"csr_matrix"}
    assert layouts[1]["indexOrder"] == [0, 2]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_three_edge_still_produces_exactly_one_id(authenticated_context: HttpContext):
    """The FIELD rule is unchanged by rank: a mask supplies one id, and the rest identify themselves."""
    mask = await _mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    store = await _store(authenticated_context, "cube", axes=(1,), shape=CUBE_SHAPE)

    result = await schema.execute(
        CREATE,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "name": "expression over time",
                "axes": _cube_axes(mask, features, timepoints),
                "store": str(store.pk),
            }
        },
    )
    assert not result.errors, result.errors

    def edges() -> list[models.Transformation]:
        system = models.CoordinateSystem.objects.get(pk=result.data["createSparseDataset"]["coordinateSystem"]["id"])
        return list(models.Transformation.objects.filter(output=system, kind=enums.TransformKind.FIELD.value))

    field_edges = await sync_to_async(edges)()
    assert len(field_edges) == 1
    assert field_edges[0].output_axes == ["object"], "one place holds one id, whatever the rank of what it keys"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_three_colouring_names_a_position_along_every_identified_axis(authenticated_context: HttpContext):
    """One value per object out of a rank-three matrix, which is what a colouring is at any rank.

    `at` names a position along **both** axes the matrix identifies itself, leaving the keyed axis
    -- the one the mask supplies ids for -- as the thing the value is *per*. The read is one
    contiguous slice from a layout compressing either named axis, then a filter of the raveled
    remainder by the other position. At rank two the run is the answer and there is no filter,
    which is the only thing rank changes here.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    store = await _store(authenticated_context, "cube", axes=(0, 1, 2), shape=CUBE_SHAPE)
    dataset = await _cube_dataset(authenticated_context, mask, features, timepoints, store)

    result = await _colour(
        authenticated_context,
        lens,
        scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 3}, {"axis": "timepoint", "value": 1}], "colormap": "VIRIDIS"},
    )
    assert not result.errors, result.errors
    entry = result.data["createLabelLayer"]["labelRender"]["colorBys"][0]
    assert entry["kind"] == "SPARSE"
    assert [(position["axis"], position["value"]) for position in entry["at"]] == [("feature", 3), ("timepoint", 1)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_rank_three_slices_differing_only_in_one_position_are_two_colourings(authenticated_context: HttpContext):
    """The duplicate check hashes the whole `at`, so the second is not the first wearing a name."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    store = await _store(authenticated_context, "cube", axes=(0, 1, 2), shape=CUBE_SHAPE)
    dataset = await _cube_dataset(authenticated_context, mask, features, timepoints, store)

    result = await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=authenticated_context,
        variable_values={
            "input": {
                "lens": str(lens.id),
                "scene": str(scene.id),
                "render": {
                    "colorBys": [
                        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 3}, {"axis": "timepoint", "value": 0}], "colormap": "VIRIDIS"},
                        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 3}, {"axis": "timepoint", "value": 1}], "colormap": "VIRIDIS"},
                    ],
                    "activeColorBy": 0,
                },
            }
        },
    )
    assert not result.errors, result.errors
    assert len(result.data["createLabelLayer"]["labelRender"]["colorBys"]) == 2, "same feature, different timepoint, two colourings"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_three_colouring_needs_a_layout_on_one_named_axis_not_all(authenticated_context: HttpContext):
    """One contiguous slice is all the read needs, so one indexed named axis is all it requires.

    The matrix here is stored **only** along the keyed axis, so neither named axis has a layout and
    there is no slice to read at all -- that is the case worth refusing. With a layout on either
    named axis it is accepted, which is the half that would be hidden by demanding both.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    entry = {"kind": "SPARSE", "dataset": None, "at": [{"axis": "feature", "value": 3}, {"axis": "timepoint", "value": 1}], "colormap": "VIRIDIS"}

    # Only the keyed axis (`object`, axis 1 of CUBE_AXES) has a layout.
    keyed_only = await _store(authenticated_context, "keyed-only", axes=(1,), shape=CUBE_SHAPE)
    entry["dataset"] = await _cube_dataset(authenticated_context, mask, features, timepoints, keyed_only)
    refused = await _colour(authenticated_context, lens, scene, dict(entry))
    assert refused.errors
    message = str(refused.errors[0])
    assert "holds no layout indexed on any of ['feature', 'timepoint']" in message
    assert "scanning every byte" in message

    # One of the two named axes is enough.
    one_named = await _store(authenticated_context, "one-named", axes=(2,), shape=CUBE_SHAPE)
    entry["dataset"] = await _cube_dataset(authenticated_context, mask, features, timepoints, one_named)
    accepted = await _colour(authenticated_context, lens, scene, dict(entry))
    assert not accepted.errors, accepted.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rank_three_plan_returns_every_other_axis(authenticated_context: HttpContext):
    """A hover is one object's whole profile, and rank only changes how many numbers that is."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    features = await _features_table(authenticated_context, "features")
    timepoints = await _features_table(authenticated_context, "timepoints")
    store = await _store(authenticated_context, "cube", axes=(0, 1, 2), shape=CUBE_SHAPE)
    await _cube_dataset(authenticated_context, mask, features, timepoints, store)

    system = await sync_to_async(lambda: mask.intrinsic_coordinate_system)()
    result = await schema.execute(SPARSE_PLANS, context_value=authenticated_context, variable_values={"system": str(system.pk)})
    assert not result.errors, result.errors
    plans = [plan for plan in result.data["attributePlans"] if plan["hops"][0]["sparseDataset"]]
    assert len(plans) == 1, "one FIELD edge, one plan"

    lookup = plans[0]["hops"][0]["lookup"]
    assert lookup["keyAxis"] == "object", "bound from the sampled pixel value"
    assert lookup["valueAxes"] == ["feature", "timepoint"], "both of the others come back, raveled"
    assert lookup["sparseArray"]["path"] == "layouts/axis1", "read from the layout compressing the key axis"


async def _filter(ctx, lens, scene, entry: dict) -> object:
    return await schema.execute(
        CREATE_LABEL_LAYER,
        context_value=ctx,
        variable_values={"input": {"lens": str(lens.id), "scene": str(scene.id), "render": {"filterBys": [entry], "activeFilterBys": [0]}}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_filter_reads_one_slice_of_a_matrix(authenticated_context: HttpContext):
    """The colouring's sibling, and the invariant it restores.

    `labelFilterByOptions` and `labelColorByOptions` project the SAME walk, so a sparse
    candidate was offered to both surfaces while only the colouring could express one. The
    options query said "these are the rules the mutation accepts" and was wrong about half
    of them; this is what makes it true again.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _filter(
        authenticated_context,
        lens,
        scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 1}], "min": 5.0},
    )
    assert not result.errors, result.errors
    (rule,) = result.data["createLabelLayer"]["labelRender"]["filterBys"]
    assert rule["kind"] == "SPARSE"
    assert rule["dataset"] == dataset
    assert [(p["axis"], p["value"]) for p in rule["at"]] == [("feature", 1)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_filter_is_measured_and_takes_no_value_set(authenticated_context: HttpContext):
    """A slice is a value per object, so it is bounded — never matched against classes."""
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _filter(
        authenticated_context,
        lens,
        scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "feature", "value": 1}], "values": ["a"]},
    )
    assert result.errors
    assert "measured" in str(result.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_sparse_filter_is_refused_along_an_unindexed_axis(authenticated_context: HttpContext):
    """The same layout requirement a colouring has, through the same resolver.

    Sharing `_resolve_sparse_slice` is what keeps the two pickers from drifting: a rule the
    colouring would refuse as a scan is refused here for the same reason and in the same words.
    """
    mask, lens, scene = await _placed_mask(authenticated_context)
    dataset = await _expression(authenticated_context, mask)

    result = await _filter(
        authenticated_context,
        lens,
        scene,
        {"kind": "SPARSE", "dataset": dataset, "at": [{"axis": "object", "value": 1}], "min": 1.0},
    )
    assert result.errors
