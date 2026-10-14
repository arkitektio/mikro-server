"""A port's ``requires`` asked of the rows: the filter answers what the describers say.

``core.descriptors`` says what one object is; ``core.logic.descriptor_filter`` says which
objects are something, in SQL. A picker is only right when the two agree, so the main test
here is a parity: over a seeded organization and a list of constraints (the client's spec
aliases among them), the filter returns exactly the objects whose descriptors hold the
constraints, by a ``holds`` written the way ``arkitekt_spec.declare.descriptors`` writes it.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import enums, models
from core.logic import descriptor_filter
from mikro_server.schema import schema
from mikro_server.service import service
from tests import seed
from tests.test_derived_datasets import _derive
from tests.test_lens_geometry import _draw
from tests.test_windowed_lenses import _CONTAINERS, _create

LENSES = "query Lenses($filters: LensFilter!) { lenses(filters: $filters) { id label } }"
DATASETS = "query Datasets($filters: ArrayDatasetFilter!) { arrayDatasets(filters: $filters) { id } }"
ANNOTATIONS = "query Annotations($filters: AnnotationFilter!) { annotations(filters: $filters) { id } }"

Constraint = tuple[str, str, object]


def holds(constraint: Constraint, described: dict) -> bool:
    """Whether these descriptors satisfy the constraint. A key that is not there holds only for ``EXISTS false``."""
    key, operator, expected = constraint
    if operator == "EXISTS":
        return (key in described) != (expected is False)
    if key not in described:
        return False
    actual = described[key]
    if operator in ("EQUALS", "MATCHES"):
        return actual == expected
    if operator == "NOT_EQUALS":
        return actual != expected
    if operator == "GTE":
        return actual >= expected
    if operator == "LTE":
        return actual <= expected
    if operator == "IN":
        return actual in expected
    if operator == "NOT_IN":
        return actual not in expected
    if operator == "CONTAINS":
        return expected in actual
    raise AssertionError(operator)


def _inputs(constraints: list[Constraint]) -> list[dict]:
    return [{"key": key, "operator": operator, "value": value} for key, operator, value in constraints]


async def _ids(ctx: HttpContext, query: str, field: str, **filters: object) -> set[str]:
    result = await schema.execute(query, context_value=ctx, variable_values={"filters": filters})
    assert not result.errors, result.errors
    return {row["id"] for row in result.data[field]}


@sync_to_async
def _described(queryset) -> dict[str, dict]:  # noqa: ANN001 - a queryset of hosted rows
    """What each row states it is: its stored descriptors, which the field and a signal read."""
    return {str(row.pk): service.describe(row) for row in queryset}


@sync_to_async
def _stored_and_computed(queryset) -> tuple[dict[str, dict], dict[str, dict]]:  # noqa: ANN001 - a queryset of hosted rows
    """What each row has stored, and what its describer says of it now, run afresh."""
    rows = list(queryset)
    return {str(row.pk): service.describe(row) for row in rows}, {str(row.pk): service.structure_for(row).compute(row) for row in rows}


async def _seed(ctx: HttpContext) -> None:
    """Arrays of several shapes with crops, a label mask, a vector field, and one container of every other kind with a window."""
    czyx = [seed.axis("c", enums.AxisType.CHANNEL), *seed.ZYX_AXES]
    stack = await seed.create_array_dataset(ctx, "stack", axes=czyx, shapes=[[3, 8, 64, 64]])
    await seed.create_lens(ctx, stack, slices=[{"axis": "z", "start": 4, "stop": 5}])
    await seed.create_lens(ctx, stack, slices=[{"axis": "c", "start": 0, "stop": 1}])
    await seed.create_lens(ctx, stack, slices=[{"axis": "c", "start": 0, "stop": 1}, {"axis": "z", "start": 4, "stop": 5}])
    movie = await seed.create_array_dataset(ctx, "movie", axes=[seed.axis("t", enums.AxisType.TIME), *seed.YX_AXES], shapes=[[5, 32, 32]])
    await seed.create_lens(ctx, movie, slices=[{"axis": "t", "start": 2, "stop": 3}])
    raw = await seed.create_array_dataset(ctx, "raw", axes=seed.YX_AXES, shapes=[[64, 64]])
    derived = await _derive(ctx, "mask", axes=seed.YX_AXES, shape=[64, 64], lens=await seed.create_lens(ctx, raw), valueRelation="CATEGORIZED")
    assert not derived.errors, derived.errors
    mask = await models.ArrayDataset.objects.aget(pk=derived.data["createArrayDataset"]["id"])
    await seed.create_lens(ctx, mask, slices=[{"axis": "y", "start": 0, "stop": 8}])
    await seed.create_array_dataset(ctx, "flow", axes=[seed.axis("v", enums.AxisType.DISPLACEMENT), *seed.YX_AXES], shapes=[[2, 64, 64]])
    for make, _, axis in _CONTAINERS.values():
        container_id, mutation, field = await make(ctx)
        await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])


N_SPACE, N_TIME, N_CHANNEL_AXES, N_CHANNELS, N_TIMEPOINTS = "@mikro/n_space_axes", "@mikro/n_time_axes", "@mikro/n_channel_axes", "@mikro/n_channels", "@mikro/n_timepoints"
VALUE_KIND, SPEC, IS_WHOLE = "@mikro/value_kind", "@mikro/spec", "@mikro/is_whole"

#: The client's spec aliases (``mikro.arkitekt.specs``), then every operator and every way of reading a key.
ARRAY_CONSTRAINTS: list[list[Constraint]] = [
    [(N_SPACE, "EQUALS", 2)],  # Image
    [(N_SPACE, "EQUALS", 3)],  # Volume
    [(N_SPACE, "GTE", 4)],  # Hypervolume
    [(N_TIME, "GTE", 1)],  # Timeseries
    [(N_CHANNEL_AXES, "GTE", 1)],  # Multichannel
    [(N_TIMEPOINTS, "LTE", 1)],  # Still
    [(N_CHANNELS, "LTE", 1)],  # SingleChannel
    [(N_SPACE, "EQUALS", 3), (N_CHANNELS, "LTE", 1)],  # SingleChannelVolume
    [(N_SPACE, "EQUALS", 2), (N_TIME, "GTE", 1)],  # TimelapseImage
    [(N_SPACE, "EQUALS", 2), (N_CHANNEL_AXES, "EQUALS", 1), (N_CHANNELS, "EQUALS", 3)],  # RGBImage
    [(VALUE_KIND, "EQUALS", "categorical")],  # LabelMask
    [(VALUE_KIND, "MATCHES", "intensity")],
    [(VALUE_KIND, "IN", ["vector", "categorical"])],
    [(VALUE_KIND, "NOT_EQUALS", "intensity")],
    [(VALUE_KIND, "NOT_IN", ["intensity", "vector"])],
    [(N_SPACE, "MATCHES", 2)],
    [(N_SPACE, "NOT_EQUALS", 2)],
    [(N_SPACE, "IN", [1, 3])],
    [(N_SPACE, "NOT_IN", [2])],
    [(N_SPACE, "LTE", 2), (IS_WHOLE, "EQUALS", False)],
    [(IS_WHOLE, "EQUALS", True)],
    [(SPEC, "CONTAINS", "VOLUME")],
    [(SPEC, "CONTAINS", "IMAGE"), (SPEC, "CONTAINS", "MULTICHANNEL")],
    [(N_CHANNELS, "EXISTS", True)],
    [(N_CHANNELS, "EXISTS", False)],
]
#: Keys only some kinds state: a lens of a kind that lacks the key does not hold it.
LENS_CONSTRAINTS: list[list[Constraint]] = [
    *ARRAY_CONSTRAINTS,
    [("@mikro/n_index_axes", "GTE", 1)],
    [("@mikro/has_track_ids", "EQUALS", False)],
    [("@mikro/n_axes", "EQUALS", 2)],
    [("@mikro/has_attributes", "EXISTS", True)],
    [("@mikro/n_layouts", "GTE", 1)],
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lens_filter_offers_exactly_the_lenses_whose_descriptors_hold(authenticated_context: HttpContext):
    ctx = authenticated_context
    await _seed(ctx)
    described = await _described(models.Lens.objects.with_sources())
    assert len({tuple(sorted(d)) for d in described.values()}) >= 6, "every kind is in the seed"

    for constraints in LENS_CONSTRAINTS:
        expected = {pk for pk, descriptors in described.items() if all(holds(c, descriptors) for c in constraints)}
        assert await _ids(ctx, LENSES, "lenses", requires=_inputs(constraints)) == expected, constraints
    assert any(all(holds(c, d) for c in LENS_CONSTRAINTS[10]) for d in described.values()), "the seed holds a label mask, or the parity proves nothing about it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_dataset_filter_offers_exactly_the_datasets_whose_descriptors_hold(authenticated_context: HttpContext):
    ctx = authenticated_context
    await _seed(ctx)
    described = await _described(models.ArrayDataset.objects.all())

    for constraints in ARRAY_CONSTRAINTS:
        expected = {pk for pk, descriptors in described.items() if all(holds(c, descriptors) for c in constraints)}
        assert await _ids(ctx, DATASETS, "arrayDatasets", requires=_inputs(constraints)) == expected, constraints


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_what_is_stored_is_what_the_describer_says(authenticated_context: HttpContext):
    """Every key is stored, the value kind and the counts of shapes included: after the seeding wrote
    derivation edges and drew shapes, what each row holds is what its describer says of it now."""
    ctx = authenticated_context
    await _seed(ctx)
    for queryset in (models.Lens.objects.with_sources(), models.ArrayDataset.objects.all(), models.Annotation.objects.all(), models.AnnotationCollection.objects.all()):
        stored, computed = await _stored_and_computed(queryset)
        assert stored == computed, queryset.model.__name__
        assert all(stored.values()), f"a {queryset.model.__name__} with no descriptors stored"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_annotation_lens_is_found_by_how_many_shapes_it_holds(authenticated_context: HttpContext):
    """The count follows the shapes drawn after the lens was made, a windowed lens' the shapes drawn inside its windows."""
    ctx = authenticated_context
    make, model, axis = _CONTAINERS["ANNOTATION"]
    collection_id, mutation, field = await make(ctx)
    whole = str((await model.objects.aget(annotation_collection_id=collection_id, windows=[])).pk)
    windowed = (await _create(ctx, mutation, field, collection_id, [{"axis": axis, "min": 0, "max": 50}]))["id"]

    assert await _ids(ctx, LENSES, "lenses", requires=_inputs([("@mikro/n_annotations", "GTE", 1)])) == set()
    assert await _ids(ctx, LENSES, "lenses", requires=_inputs([("@mikro/n_annotations", "EQUALS", 0)])) == {whole, windowed}
    await _draw(ctx, collection_id, "RECTANGLE", [[1.0, 1.0], [4.0, 4.0]])
    assert await _ids(ctx, LENSES, "lenses", requires=_inputs([("@mikro/n_annotations", "GTE", 1)])) == {whole, windowed}
    await _draw(ctx, collection_id, "RECTANGLE", [[100.0, 100.0], [104.0, 104.0]])
    assert await _ids(ctx, LENSES, "lenses", requires=_inputs([("@mikro/n_annotations", "EQUALS", 2)])) == {whole}, "the second shape lies outside the window"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_annotations_are_found_by_their_kind_and_the_axes_of_their_space(authenticated_context: HttpContext):
    """What a port typed `Path` asks: the kind as the descriptor spells it, and the axis counts of the collection's drawing space."""
    ctx = authenticated_context
    make, _, _ = _CONTAINERS["ANNOTATION"]
    collection_id, _, _ = await make(ctx)
    box = (await _draw(ctx, collection_id, "RECTANGLE", [[1.0, 1.0], [4.0, 4.0]]))["id"]
    path = (await _draw(ctx, collection_id, "PATH", [[1.0, 1.0], [4.0, 4.0], [6.0, 2.0]]))["id"]
    described = await _described(models.Annotation.objects.select_related("collection__coordinate_system"))
    n_space = described[path]["@mikro/n_space_axes"]

    constraints: list[list[Constraint]] = [
        [("@mikro/annotation_kind", "EQUALS", "PATH")],
        [("@mikro/annotation_kind", "MATCHES", "PATH")],
        [("@mikro/annotation_kind", "NOT_EQUALS", "PATH")],
        [("@mikro/annotation_kind", "IN", ["PATH", "POLYGON"])],
        [("@mikro/annotation_kind", "NOT_IN", ["PATH", "POLYGON"])],
        [("@mikro/annotation_kind", "EQUALS", "SPIRAL")],
        [("@mikro/annotation_kind", "EQUALS", "PATH"), ("@mikro/n_space_axes", "EQUALS", n_space)],
        [("@mikro/annotation_kind", "EQUALS", "PATH"), ("@mikro/n_space_axes", "EQUALS", n_space + 1)],
        [("@mikro/n_space_axes", "GTE", 1)],
        [("@mikro/n_time_axes", "LTE", 0)],
        [("@mikro/n_time_axes", "GTE", 1)],
    ]
    for wanted in constraints:
        expected = {pk for pk, descriptors in described.items() if all(holds(c, descriptors) for c in wanted)}
        assert await _ids(ctx, ANNOTATIONS, "annotations", requires=_inputs(wanted)) == expected, wanted
    assert await _ids(ctx, ANNOTATIONS, "annotations", requires=_inputs(constraints[0])) == {path} != {box}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_key_the_structure_does_not_declare_is_refused(authenticated_context: HttpContext):
    """Dropping a constraint would offer objects the port then refuses, so an unknown key is an error and never a no-op."""
    ctx = authenticated_context
    for query, key in ((LENSES, "@mikro/annotation_kind"), (DATASETS, "@mikro/n_index_axes"), (ANNOTATIONS, "@mikro/n_channels")):
        result = await schema.execute(query, context_value=ctx, variable_values={"filters": {"requires": _inputs([(key, "EQUALS", 1)])}})
        assert result.errors and "is not a descriptor here" in str(result.errors[0]), (query, result.errors)
    wrong_type = await schema.execute(LENSES, context_value=ctx, variable_values={"filters": {"requires": _inputs([(N_SPACE, "GTE", "three")])}})
    assert wrong_type.errors and "takes a number" in str(wrong_type.errors[0])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_requires_composes_with_the_other_filters_and_pages(authenticated_context: HttpContext, other_org_context: HttpContext):
    """A picker's query: a kind, a search, the port's constraints, a page -- and nothing of another organization."""
    ctx = authenticated_context
    await _seed(ctx)
    # Their own volume only: the windowed containers' stores are keyed globally and cannot be seeded twice.
    await seed.create_array_dataset(other_org_context, "theirs", axes=[seed.axis("c", enums.AxisType.CHANNEL), *seed.ZYX_AXES], shapes=[[3, 8, 64, 64]])
    volumes = _inputs([(N_SPACE, "EQUALS", 3)])
    mine = await _ids(ctx, LENSES, "lenses", requires=volumes, kind="ARRAY")
    theirs = await _ids(other_org_context, LENSES, "lenses", requires=volumes, kind="ARRAY")
    assert mine and theirs and not mine & theirs

    paged = "query Lenses($filters: LensFilter!, $pagination: OffsetPaginationInput) { lenses(filters: $filters, pagination: $pagination, ordering: [{id: ASC}]) { id label } }"
    pages: list[str] = []
    for offset in range(0, len(mine) + 1):
        result = await schema.execute(paged, context_value=ctx, variable_values={"filters": {"requires": volumes, "kind": "ARRAY"}, "pagination": {"limit": 1, "offset": offset}})
        assert not result.errors, result.errors
        pages += [row["id"] for row in result.data["lenses"]]
        assert all(row["label"] for row in result.data["lenses"]), "a lens nobody named still has something to be called"
    assert sorted(pages, key=int) == sorted(mine, key=int), "paging walks exactly the filtered set"
    assert await _ids(ctx, LENSES, "lenses", requires=volumes, kind="ARRAY", search="stack") == mine
    assert await _ids(ctx, LENSES, "lenses", requires=volumes, kind="TABLE") == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_equality_constraint_is_answerable_from_the_descriptor_index(authenticated_context: HttpContext):
    """The compiled condition is one the GIN index can serve. Sequential scans are switched off: on a handful of rows the planner would never choose an index."""
    await _seed(authenticated_context)

    @sync_to_async
    def plan() -> str:
        from django.db import connection, transaction

        queryset, condition = descriptor_filter.requires(
            models.Lens, models.Lens.objects.non_polymorphic(), [descriptor_filter.DescriptorConstraintInput(key=N_SPACE, operator=descriptor_filter.DescriptorOperator.EQUALS, value=3)]
        )
        sql, params = queryset.filter(condition).values("pk").query.sql_with_params()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(f"EXPLAIN {sql}", params)
            return "\n".join(row[0] for row in cursor.fetchall())

    assert "lens_descriptors_gin" in await plan()
