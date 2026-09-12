"""Filter tests for the multi-dimensional data system queries
(ArrayDataset, Scene, Layer, Lens, Annotation)."""

import pytest
from asgiref.sync import sync_to_async

from core import enums
from core.models import ArrayDataset, Annotation, AnnotationCollection, CoordinateSystem, Layer, Lens, Scene, Transformation
from kante.context import HttpContext
from mikro_server.schema import schema

from tests import seed
from tests.seed import create_other_user

# Ordered by type -- time, then channel, then space -- which RFC-5 requires.
_TCZYX = [
    seed.axis("t", enums.AxisType.TIME),
    seed.axis("c", enums.AxisType.CHANNEL),
    seed.axis("z", enums.AxisType.SPACE),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

# A lambda stack: a SPECTRUM axis ranks with channel, so it sorts before space.
_LZYX = [
    seed.axis("l", enums.AxisType.SPECTRUM),
    seed.axis("z", enums.AxisType.SPACE),
    seed.axis("y", enums.AxisType.SPACE),
    seed.axis("x", enums.AxisType.SPACE),
]

# A line profile: one spatial axis.
_X = [seed.axis("x", enums.AxisType.SPACE)]

# No spatial extent and no acquisition modifier: a single INDEX axis. Its spec is exactly
# [SCALAR] -- distinct from a headless dataset, whose spec is the empty list.
_INDEX = [seed.axis("i", enums.AxisType.INDEX)]


async def execute(ctx, query, filters):
    result = await schema.execute(query, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return result.data


async def create_array_dataset(ctx, name, **kwargs):
    creator = kwargs.pop("creator", None)
    dataset = await seed.create_array_dataset(ctx, name, shapes=[[1, 1, 1, 100, 100]], axes=_TCZYX)
    if creator is not None or kwargs:
        for field, value in {**kwargs, **({"creator": creator} if creator is not None else {})}.items():
            setattr(dataset, field, value)
        await dataset.asave()
    return dataset


async def create_lens(dataset):
    return await Lens.objects.acreate(dataset=dataset, slices=[])


async def create_scene(ctx, name, **kwargs):
    # Scene.world is non-null: create a bare shared world the same way the seed helper does.
    from core.models import CoordinateSystem

    world = await CoordinateSystem.objects.acreate(name=f"{name}/world", organization=ctx.request.organization)
    return await Scene.objects.acreate(name=name, world=world, organization=ctx.request.organization, **kwargs)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_filters(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    other = await create_other_user(ctx)
    await create_array_dataset(ctx, "Acquisition", description="raw stack")
    await create_array_dataset(ctx, "Processed", creator=other)

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { id name }
        }
    """

    data = await execute(ctx, query, {"search": "acq"})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Acquisition"}

    data = await execute(ctx, query, {"owner": "2"})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Processed"}

    data = await execute(ctx, query, {"description": {"iContains": "raw"}})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Acquisition"}


async def _seed_spec_datasets(ctx):
    """One dataset per shape of interest, named for what it is."""
    await seed.create_array_dataset(ctx, "Stack", shapes=[[1, 1, 4, 100, 100]], axes=_TCZYX)
    await seed.create_array_dataset(ctx, "Plane", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)
    await seed.create_array_dataset(ctx, "Bare", shapes=[[64, 64]], axes=seed.YX_AXES)
    await seed.create_array_dataset(ctx, "Lambda", shapes=[[8, 4, 64, 64]], axes=_LZYX)
    await seed.create_array_dataset(ctx, "Profile", shapes=[[64]], axes=_X)
    await seed.create_array_dataset(ctx, "Point", shapes=[[10]], axes=_INDEX)


_SPEC_QUERY = """
    query List($filters: ArrayDatasetFilter) {
        arrayDatasets(filters: $filters) { name spec }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_spec_field(db, authenticated_context: HttpContext):
    """The field reports every spec that holds, spatial member first."""
    ctx = authenticated_context
    await _seed_spec_datasets(ctx)

    data = await execute(ctx, _SPEC_QUERY, {})
    specs = {d["name"]: d["spec"] for d in data["arrayDatasets"]}

    assert specs["Stack"] == ["VOLUME", "TIMESERIES", "MULTICHANNEL"]
    assert specs["Plane"] == ["IMAGE", "MULTICHANNEL"]
    assert specs["Bare"] == ["IMAGE"]
    assert specs["Lambda"] == ["VOLUME", "SPECTRAL"]
    assert specs["Profile"] == ["PROFILE"]
    assert specs["Point"] == ["SCALAR"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_spec_filter(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    await _seed_spec_datasets(ctx)

    async def names(filters):
        data = await execute(ctx, _SPEC_QUERY, filters)
        return {d["name"] for d in data["arrayDatasets"]}

    # A spatial member is a count of SPACE axes, so it partitions the datasets.
    assert await names({"spec": ["VOLUME"]}) == {"Stack", "Lambda"}
    assert await names({"spec": ["IMAGE"]}) == {"Plane", "Bare"}
    assert await names({"spec": ["PROFILE"]}) == {"Profile"}
    assert await names({"spec": ["SCALAR"]}) == {"Point"}
    assert await names({"spec": ["HYPERVOLUME"]}) == set()

    # Modifiers stack on top of a spatial member.
    assert await names({"spec": ["VOLUME", "TIMESERIES"]}) == {"Stack"}
    assert await names({"spec": ["VOLUME", "SPECTRAL"]}) == {"Lambda"}
    assert await names({"spec": ["MULTICHANNEL"]}) == {"Stack", "Plane"}

    # Two modifiers must both hold. Naively ANDing the Qs would ask one axis row to
    # be TIME *and* CHANNEL at once and match nothing -- this is the regression test
    # for that, so it must find the dataset that carries both.
    assert await names({"spec": ["TIMESERIES", "MULTICHANNEL"]}) == {"Stack"}
    assert await names({"spec": ["TIMESERIES", "SPECTRAL"]}) == set()

    # Only one spatial member can hold, so asking for two is answerable and empty.
    assert await names({"spec": ["IMAGE", "VOLUME"]}) == set()

    # An empty list constrains nothing.
    assert await names({"spec": []}) == {"Stack", "Plane", "Bare", "Lambda", "Profile", "Point"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_spec_filter_ignores_datasets_without_an_intrinsic_system(db, authenticated_context: HttpContext):
    """A dataset whose axes are unknown answers to no spatial spec -- least of all SCALAR.

    Its `stored_spec` is the empty list (nothing materialized it), so the `spec` field
    reports nothing and `@>` containment of any non-empty request excludes it. This is
    what keeps it distinct from a genuine SCALAR dataset (a no-SPACE-axis one, like
    Point), whose `stored_spec` is `["SCALAR"]`.
    """
    ctx = authenticated_context
    await _seed_spec_datasets(ctx)
    await ArrayDataset.objects.acreate(name="Headless", organization=ctx.request.organization)

    data = await execute(ctx, _SPEC_QUERY, {})
    assert {d["name"]: d["spec"] for d in data["arrayDatasets"]}["Headless"] == []

    # SCALAR finds the real no-SPACE-axis dataset, never the headless one.
    names = {d["name"] for d in (await execute(ctx, _SPEC_QUERY, {"spec": ["SCALAR"]}))["arrayDatasets"]}
    assert names == {"Point"}
    assert "Headless" not in names


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_spec_is_materialized_at_creation(db, authenticated_context: HttpContext):
    """The spec is written onto the column when the axes are, not derived on read.

    Asserts both faces of the contract: the raw `stored_spec` strings on the row, and the
    `spec` property coercing them back to enum members. Re-reads from the DB so the check
    does not lean on the in-memory instance the writer happened to touch.
    """
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Stack", shapes=[[1, 1, 4, 100, 100]], axes=_TCZYX)

    fresh = await ArrayDataset.objects.aget(pk=dataset.pk)
    assert fresh.stored_spec == ["VOLUME", "TIMESERIES", "MULTICHANNEL"]
    assert fresh.spec == [enums.ArrayDatasetSpec.VOLUME, enums.ArrayDatasetSpec.TIMESERIES, enums.ArrayDatasetSpec.MULTICHANNEL]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_spec_filter_composes_under_and_or(db, authenticated_context: HttpContext):
    """`AND`/`OR` recurse with the same prefix onto one queryset, and the two filters must
    still compose correctly there.

    `spec` reads the materialized `stored_spec` column, so it composes trivially -- each
    branch is an independent `@>` containment Q. `has_axis_types` still annotates, and an
    alias that named only its prefix would collide: Django keeps the first annotation and
    drops the second, while the second branch's Q reads the alias anyway and silently tests
    the first branch's expression. That returns wrong rows rather than raising, which is why
    these assert membership and not just "no error".
    """
    ctx = authenticated_context
    await _seed_spec_datasets(ctx)

    async def names(filters):
        data = await execute(ctx, _SPEC_QUERY, filters)
        return {d["name"] for d in data["arrayDatasets"]}

    # Two different modifier sets: distinct expressions, so distinct aliases.
    assert await names({"spec": ["MULTICHANNEL"], "AND": {"spec": ["TIMESERIES"]}}) == {"Stack"}
    assert await names({"hasAxisTypes": ["SPACE"], "AND": {"hasAxisTypes": ["CHANNEL"]}}) == {"Plane", "Stack"}
    # The same expression twice: one shared annotation, and the answer is unchanged.
    assert await names({"spec": ["IMAGE"], "AND": {"spec": ["IMAGE"]}}) == {"Bare", "Plane"}
    assert await names({"spec": ["IMAGE"], "AND": {"spec": ["VOLUME"]}}) == set()
    assert await names({"spec": ["IMAGE"], "OR": {"spec": ["PROFILE"]}}) == {"Bare", "Plane", "Profile"}
    # The two filters side by side annotate the same queryset too.
    assert await names({"spec": ["MULTICHANNEL"], "hasAxisTypes": ["TIME"]}) == {"Stack"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_has_axis_types_filter(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    await _seed_spec_datasets(ctx)

    async def names(filters):
        data = await execute(ctx, _SPEC_QUERY, filters)
        return {d["name"] for d in data["arrayDatasets"]}

    assert await names({"hasAxisTypes": ["TIME"]}) == {"Stack"}
    assert await names({"hasAxisTypes": ["SPECTRUM"]}) == {"Lambda"}
    # All-of, again the same-relation AND case.
    assert await names({"hasAxisTypes": ["TIME", "CHANNEL"]}) == {"Stack"}
    assert await names({"hasAxisTypes": ["CHANNEL", "SPECTRUM"]}) == set()
    assert await names({"hasAxisTypes": ["SPACE"]}) == {"Stack", "Plane", "Bare", "Lambda", "Profile"}
    assert await names({"hasAxisTypes": []}) == {"Stack", "Plane", "Bare", "Lambda", "Profile", "Point"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_multiscale_filter(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    await seed.create_array_dataset(ctx, "Pyramid", shapes=[[3, 64, 64], [3, 32, 32], [3, 16, 16]], axes=seed.SIMPLE_AXES)
    await seed.create_array_dataset(ctx, "Flat", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name multiscale }
        }
    """
    data = await execute(ctx, query, {"multiscale": True})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Pyramid"}

    data = await execute(ctx, query, {"multiscale": False})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Flat"}

    # The filter must say what the field says, for every dataset.
    data = await execute(ctx, query, {})
    assert {d["name"]: d["multiscale"] for d in data["arrayDatasets"]} == {"Pyramid": True, "Flat": False}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_has_physical_space_filter(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    calibrated = await seed.create_array_dataset(ctx, "Calibrated", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)
    await seed.create_array_dataset(ctx, "Pixels", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)
    await seed.create_physical_space(
        ctx,
        calibrated,
        axes=[
            seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."),
            seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
            seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
        ],
        scale=[1.0, 0.5, 0.5],
    )

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name }
        }
    """
    data = await execute(ctx, query, {"hasPhysicalSpace": True})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Calibrated"}

    data = await execute(ctx, query, {"hasPhysicalSpace": False})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Pixels"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_has_physical_space_filter_does_not_duplicate_on_several_spaces(db, authenticated_context: HttpContext):
    """A dataset carries stage space, specimen space, a refined edge -- and is still one row."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Multi", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)
    axes = [
        seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."),
        seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
        seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
    ]
    await seed.create_physical_space(ctx, dataset, axes=axes, scale=[1.0, 0.5, 0.5], name="stage")
    await seed.create_physical_space(ctx, dataset, axes=axes, scale=[1.0, 0.2, 0.2], name="specimen")

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name }
        }
    """
    data = await execute(ctx, query, {"hasPhysicalSpace": True})
    assert [d["name"] for d in data["arrayDatasets"]] == ["Multi"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_scene_filter(db, authenticated_context: HttpContext):
    """`scene` is what is staged, and it reaches the dataset through its lenses' layers."""
    ctx = authenticated_context
    staged = await create_array_dataset(ctx, "Staged")
    await create_array_dataset(ctx, "Unstaged")
    scene = await create_scene(ctx, "Composition")
    lens = await create_lens(staged)
    await Layer.objects.acreate(scene=scene, kind=enums.LayerKindChoices.IMAGE.value, lens=lens, blending=enums.BlendingChoices.NORMAL.value)

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name }
        }
    """
    data = await execute(ctx, query, {"scene": str(scene.id)})
    assert {d["name"] for d in data["arrayDatasets"]} == {"Staged"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_scene_filter_does_not_duplicate_on_several_layers(db, authenticated_context: HttpContext):
    """Two lenses of one dataset staged in one scene: two layers, still one dataset."""
    ctx = authenticated_context
    staged = await create_array_dataset(ctx, "Staged")
    scene = await create_scene(ctx, "Composition")
    for _ in range(2):
        lens = await create_lens(staged)
        await Layer.objects.acreate(scene=scene, kind=enums.LayerKindChoices.IMAGE.value, lens=lens, blending=enums.BlendingChoices.NORMAL.value)

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name }
        }
    """
    data = await execute(ctx, query, {"scene": str(scene.id)})
    assert [d["name"] for d in data["arrayDatasets"]] == ["Staged"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_array_dataset_filters_combine_over_multiplying_joins(db, authenticated_context: HttpContext):
    """The counting filters must survive the joins the others add.

    `hasPhysicalSpace` and `scene` each join a to-many relation and call `.distinct()`, while
    `spec`, `hasAxisTypes` and `multiscale` count over their own. Combine them and every
    join multiplies the rows the counts see -- which is what `distinct=True` inside each
    Count is for. This dataset has two physical spaces, two staged layers and two pyramid
    levels, so a count that missed it would be off by a factor, not by one.
    """
    ctx = authenticated_context
    calibrated_axes = [
        seed.physical_axis("c", enums.AxisType.CHANNEL, "a.u."),
        seed.physical_axis("y", enums.AxisType.SPACE, "micrometer"),
        seed.physical_axis("x", enums.AxisType.SPACE, "micrometer"),
    ]
    rich = await seed.create_array_dataset(ctx, "Rich", shapes=[[3, 64, 64], [3, 32, 32]], axes=seed.SIMPLE_AXES)
    await seed.create_physical_space(ctx, rich, axes=calibrated_axes, scale=[1.0, 0.5, 0.5], name="stage")
    await seed.create_physical_space(ctx, rich, axes=calibrated_axes, scale=[1.0, 0.2, 0.2], name="specimen")
    scene = await create_scene(ctx, "Composition")
    for _ in range(2):
        lens = await create_lens(rich)
        await Layer.objects.acreate(scene=scene, kind=enums.LayerKindChoices.IMAGE.value, lens=lens, blending=enums.BlendingChoices.NORMAL.value)
    await seed.create_array_dataset(ctx, "Plain", shapes=[[3, 64, 64]], axes=seed.SIMPLE_AXES)

    query = """
        query List($filters: ArrayDatasetFilter) {
            arrayDatasets(filters: $filters) { name }
        }
    """

    async def names(filters):
        data = await execute(ctx, query, filters)
        return [d["name"] for d in data["arrayDatasets"]]

    assert await names({"spec": ["IMAGE"], "hasPhysicalSpace": True}) == ["Rich"]
    assert await names({"spec": ["IMAGE"], "scene": str(scene.id)}) == ["Rich"]
    assert await names({"multiscale": True, "hasPhysicalSpace": True}) == ["Rich"]
    assert await names({"hasAxisTypes": ["CHANNEL", "SPACE"], "scene": str(scene.id)}) == ["Rich"]
    assert await names({"spec": ["IMAGE", "MULTICHANNEL"], "hasPhysicalSpace": True, "scene": str(scene.id), "multiscale": True}) == ["Rich"]
    # The spatial count is still a count, not a row tally inflated by the joins.
    assert await names({"spec": ["VOLUME"], "hasPhysicalSpace": True}) == []
    assert sorted(await names({"spec": ["IMAGE"]})) == ["Plain", "Rich"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_scene_filters(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    await create_scene(ctx, "RootScene")
    await create_scene(ctx, "SubScene")

    query = """
        query List($filters: SceneFilter) {
            scenes(filters: $filters) { id name }
        }
    """

    data = await execute(ctx, query, {"search": "sub"})
    assert {s["name"] for s in data["scenes"]} == {"SubScene"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_layer_filters(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    array_dataset = await create_array_dataset(ctx, "ADS")
    lens_a = await create_lens(array_dataset)
    lens_b = await create_lens(array_dataset)
    scene_a = await create_scene(ctx, "SceneA")
    scene_b = await create_scene(ctx, "SceneB")

    additive = await Layer.objects.acreate(
        scene=scene_a, kind=enums.LayerKindChoices.IMAGE.value, lens=lens_a, blending=enums.BlendingChoices.ADDITIVE.value
    )
    overlaid = await Layer.objects.acreate(
        scene=scene_b, kind=enums.LayerKindChoices.IMAGE.value, lens=lens_b, blending=enums.BlendingChoices.NORMAL.value
    )

    query = """
        query List($filters: LayerFilter) {
            layers(filters: $filters) { id }
        }
    """

    data = await execute(ctx, query, {"blending": "NORMAL"})
    assert {layer["id"] for layer in data["layers"]} == {str(overlaid.id)}

    data = await execute(ctx, query, {"scene": str(scene_b.id)})
    assert {layer["id"] for layer in data["layers"]} == {str(overlaid.id)}

    data = await execute(ctx, query, {"lens": str(lens_a.id)})
    assert {layer["id"] for layer in data["layers"]} == {str(additive.id)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_lens_filter_by_dataset(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    ds_a = await create_array_dataset(ctx, "A")
    ds_b = await create_array_dataset(ctx, "B")
    lens_a = await create_lens(ds_a)
    await create_lens(ds_b)

    query = """
        query List($filters: LensFilter) {
            lenses(filters: $filters) { id }
        }
    """
    data = await execute(ctx, query, {"dataset": str(ds_a.id)})
    assert {lens["id"] for lens in data["lenses"]} == {str(lens_a.id)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_annotation_filters(db, authenticated_context: HttpContext):
    ctx = authenticated_context
    ds_a = await create_array_dataset(ctx, "A")
    ds_b = await create_array_dataset(ctx, "B")

    def seed_collection(name: str, dataset: ArrayDataset) -> AnnotationCollection:
        # An annotation lives in its collection's own system; the `dataset` filter
        # resolves through the collection's derivation edge, so the fixture authors one.
        system = CoordinateSystem.objects.create(name=f"{name}/drawing", organization=ctx.request.organization)
        collection = AnnotationCollection.objects.create(name=name, coordinate_system=system, organization=ctx.request.organization)
        Transformation.objects.create(
            kind=enums.TransformKindChoices.IDENTITY.value,
            input=system,
            output=dataset.intrinsic_coordinate_system,
            organization=ctx.request.organization,
        )
        return collection

    collection_a = await sync_to_async(seed_collection)("CollA", ds_a)
    collection_b = await sync_to_async(seed_collection)("CollB", ds_b)
    system_b = await sync_to_async(lambda: collection_b.coordinate_system)()
    await Annotation.objects.acreate(
        collection=collection_a,
        name="LeftRect",
        vectors=[[0.0, 0.0, 0.0], [0.0, 10.0, 10.0]],
        kind=enums.AnnotationKindChoices.RECTANGLE.value,
    )
    await Annotation.objects.acreate(
        collection=collection_b,
        name="RightPoly",
        vectors=[[0.0, 100.0, 100.0], [0.0, 200.0, 200.0]],
        kind=enums.AnnotationKindChoices.POLYGON.value,
    )

    query = """
        query List($filters: AnnotationFilter) {
            annotations(filters: $filters) { id name }
        }
    """

    data = await execute(ctx, query, {"kind": "RECTANGLE"})
    assert {r["name"] for r in data["annotations"]} == {"LeftRect"}

    # `dataset` still filters, now through the collection's derivation edge.
    data = await execute(ctx, query, {"dataset": str(ds_a.id)})
    assert {r["name"] for r in data["annotations"]} == {"LeftRect"}

    data = await execute(ctx, query, {"coordinateSystem": str(system_b.id)})
    assert {r["name"] for r in data["annotations"]} == {"RightPoly"}

    data = await execute(ctx, query, {"collection": str(collection_b.id)})
    assert {r["name"] for r in data["annotations"]} == {"RightPoly"}

    data = await execute(ctx, query, {"search": "poly"})
    assert {r["name"] for r in data["annotations"]} == {"RightPoly"}
