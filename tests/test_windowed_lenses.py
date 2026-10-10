"""A lens over a container that is not an array: one row per (container, normalized windows), in the container's own space.

Every table, sparse dataset, mesh collection, network collection and annotation collection
has its whole lens from creation. A windowed lens keeps and re-indexes nothing, so it lives in
the container's coordinate system, mints no system and no edge, and the same windows asked
for twice are the same id.
"""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_column_options import _collection as _mesh_collection
from tests.test_column_options import _table
from tests.test_layer_kinds import _seed_table_dataset
from tests.test_lens_geometry import _collection_on
from tests.test_network_layers import _collection as _network_collection
from tests.test_sparse_datasets import CREATE as CREATE_SPARSE
from tests.test_sparse_datasets import _axes, _features_table, _mask, _store

LENS = """
query Lens($id: ID!) {
  lens(id: $id) {
    id kind
    coordinateSystem { id }
    ... on TableLens { tableDataset { id } windows { axis min max } }
    ... on SparseLens { sparseDataset { id } windows { axis min max } }
    ... on MeshLens { meshCollection { id } windows { axis min max } }
    ... on NetworkLens { networkCollection { id } windows { axis min max } }
    ... on AnnotationLens { annotationCollection { id } windows { axis min max } }
  }
}
"""

LENSES_OF_KIND = "query Lenses($kind: LensKind!) { lenses(filters: { kind: $kind }) { id kind } }"


async def _read(ctx: HttpContext, lens_id: str) -> dict:
    result = await schema.execute(LENS, context_value=ctx, variable_values={"id": lens_id})
    assert not result.errors, result.errors
    return result.data["lens"]


async def _create(ctx: HttpContext, mutation: str, field: str, container_id: str, windows: list[dict] | None) -> dict | list:
    result = await schema.execute(
        f"mutation Make($input: {mutation}Input!) {{ {mutation[0].lower() + mutation[1:]}(input: $input) {{ id kind coordinateSystem {{ id }} }} }}",
        context_value=ctx,
        variable_values={"input": {field: container_id, "windows": windows or []}},
    )
    if result.errors:
        return result.errors
    return result.data[mutation[0].lower() + mutation[1:]]


async def _systems() -> int:
    return await sync_to_async(models.CoordinateSystem.objects.count)()


# --- the containers, each with its whole lens from creation ------------------------------------


async def _a_table(ctx: HttpContext) -> tuple[str, str, str]:
    """A table with a time and a position column: id, mutation, input field."""
    table_id = await _table(ctx, "tracks", [{"name": "t", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "TIME", "unit": "second"}, {"name": "x", "dtype": "DOUBLE", "role": "COORDINATE", "axisType": "SPACE", "unit": "micrometer"}, {"name": "speed", "dtype": "DOUBLE", "role": "ATTRIBUTE"}])
    return table_id, "CreateTableLens", "tableDataset"


async def _a_sparse(ctx: HttpContext) -> tuple[str, str, str]:
    mask = await _mask(ctx)
    features = await _features_table(ctx)
    store = await _store(ctx, "matrix", axes=(0, 1))
    result = await schema.execute(CREATE_SPARSE, context_value=ctx, variable_values={"input": {"name": "expression", "store": str(store.pk), "axes": _axes(mask, features)}})
    assert not result.errors, result.errors
    return result.data["createSparseDataset"]["id"], "CreateSparseLens", "sparseDataset"


async def _a_mesh(ctx: HttpContext) -> tuple[str, str, str]:
    return str((await _mesh_collection(ctx)).pk), "CreateMeshLens", "meshCollection"


async def _a_network(ctx: HttpContext) -> tuple[str, str, str]:
    return str((await _network_collection(ctx)).pk), "CreateNetworkLens", "networkCollection"


async def _an_annotation_collection(ctx: HttpContext) -> tuple[str, str, str]:
    dataset = await seed.create_array_dataset(ctx, "Drawn on", axes=seed.YX_AXES, shapes=[[64, 64]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    return collection["id"], "CreateAnnotationLens", "annotationCollection"


_CONTAINERS = {
    "TABLE": (_a_table, models.TableLens, "t"),
    "SPARSE": (_a_sparse, models.SparseLens, "feature"),
    "MESH": (_a_mesh, models.MeshLens, "z"),
    "NETWORK": (_a_network, models.NetworkLens, "x"),
    "ANNOTATION": (_an_annotation_collection, models.AnnotationLens, "y"),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_every_container_has_its_whole_lens_from_creation_and_it_is_the_one_asked_for(authenticated_context: HttpContext, kind: str):
    """Creating the container made its whole lens, in its own space; asking for no windows, or windows that keep everything, is that lens."""
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)

    rows = [row async for row in model.objects.filter(**{model.CONTAINER_FIELD + "_id": container_id})]
    assert len(rows) == 1 and rows[0].windows == [], "exactly the whole lens"
    whole = rows[0]
    container = await sync_to_async(lambda: whole.container)()
    assert whole.coordinate_system_id == container.coordinate_system_id, "it lives in the container's own space"

    before = await _systems()
    bare = await _create(ctx, mutation, field, container_id, [])
    open_window = await _create(ctx, mutation, field, container_id, [{"axis": axis}])
    assert bare["id"] == open_window["id"] == str(whole.pk)
    assert await _systems() == before, "no space is minted for a selection that re-indexes nothing"

    read = await _read(ctx, bare["id"])
    assert read["kind"] == kind and read["windows"] == []
    assert read["coordinateSystem"]["id"] == str(container.coordinate_system_id)

    result = await schema.execute(LENSES_OF_KIND, context_value=ctx, variable_values={"kind": kind})
    assert not result.errors, result.errors
    assert {lens["id"] for lens in result.data["lenses"]} == {str(whole.pk)}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_the_same_windows_are_the_same_lens_however_spelled(authenticated_context: HttpContext, kind: str):
    """Two calls for one window are one id, stored as floats in the container's axis order, and no system is minted."""
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)
    before = await _systems()

    first = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 2, "max": 10}])
    second = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 2.0, "max": 10.0}])
    half_open = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 2}])

    assert first["id"] == second["id"] != half_open["id"]
    assert await _systems() == before
    read = await _read(ctx, first["id"])
    assert read["windows"] == [{"axis": axis, "min": 2.0, "max": 10.0}]
    assert (await _read(ctx, half_open["id"]))["windows"] == [{"axis": axis, "min": 2.0, "max": None}]
    assert await sync_to_async(model.objects.filter(**{model.CONTAINER_FIELD + "_id": container_id}).count)() == 3, "the whole lens and the two windows"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_that_cannot_be_made_is_refused_in_prose(authenticated_context: HttpContext):
    """An axis the container lacks, an axis windowed twice and an empty window are refused, and nothing is written."""
    ctx = authenticated_context
    table_id, mutation, field = await _a_table(ctx)
    before = await sync_to_async(models.TableLens.objects.count)()

    for windows, message in [
        ([{"axis": "z", "min": 0, "max": 1}], "does not have"),
        ([{"axis": "t", "min": 0, "max": 1}, {"axis": "t", "min": 1, "max": 2}], "windowed twice"),
        ([{"axis": "t", "min": 9, "max": 1}], "keeps nothing"),
    ]:
        errors = await _create(ctx, mutation, field, table_id, windows)
        assert isinstance(errors, list) and message in errors[0].message, (windows, errors)

    assert await sync_to_async(models.TableLens.objects.count)() == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_windows_are_stored_in_the_containers_axis_order(authenticated_context: HttpContext):
    """The order a caller lists windows in is not part of the selection."""
    ctx = authenticated_context
    table_id, mutation, field = await _a_table(ctx)

    xt = await _create(ctx, mutation, field, table_id, [{"axis": "x", "min": 0, "max": 50}, {"axis": "t", "min": 1, "max": 2}])
    tx = await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 1, "max": 2}, {"axis": "x", "min": 0, "max": 50}])

    assert xt["id"] == tx["id"]
    assert [window["axis"] for window in (await _read(ctx, xt["id"]))["windows"]] == ["t", "x"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_windowed_lens_is_a_resident_of_its_containers_space_that_the_container_speaks_for(authenticated_context: HttpContext):
    """It shows among the space's residents as its kind, and `deleteLens` refuses the whole lens and allows a window nothing draws through."""
    ctx = authenticated_context
    table_id, mutation, field = await _a_table(ctx)
    whole = await models.TableLens.objects.aget(table_dataset_id=table_id)
    window = await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 0, "max": 1}])

    result = await schema.execute("query S($id: ID!) { coordinateSystem(id: $id) { residents { __typename } } }", context_value=ctx, variable_values={"id": str(whole.coordinate_system_id)})
    assert not result.errors, result.errors
    assert sorted(r["__typename"] for r in result.data["coordinateSystem"]["residents"]) == ["TableDataset", "TableLens"], "the table once, and the window beside it; the whole lens is the table's handle, not a second resident"

    mutation_text = "mutation($id: ID!) { deleteLens(input: {id: $id}) }"
    refused = await schema.execute(mutation_text, variable_values={"id": str(whole.pk)}, context_value=ctx)
    assert refused.errors and "whole" in refused.errors[0].message
    allowed = await schema.execute(mutation_text, variable_values={"id": window["id"]}, context_value=ctx)
    assert not allowed.errors, allowed.errors
    assert not await models.Lens.objects.filter(pk=window["id"]).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_is_found_by_its_containers_label_whatever_the_container(authenticated_context: HttpContext):
    """A sparse dataset by its name; a mesh and a network collection by their version, which is the label they have."""
    ctx = authenticated_context
    sparse_id, _, _ = await _a_sparse(ctx)
    mesh_id, _, _ = await _a_mesh(ctx)
    network_id, _, _ = await _a_network(ctx)

    async def found(search: str) -> set[tuple[str, str]]:
        result = await schema.execute("query S($search: String!) { lenses(filters: { search: $search }) { kind id } }", context_value=ctx, variable_values={"search": search})
        assert not result.errors, result.errors
        return {(lens["kind"], lens["id"]) for lens in result.data["lenses"]}

    sparse_whole = await models.SparseLens.objects.aget(sparse_dataset_id=sparse_id)
    assert await found("expressi") == {("SPARSE", str(sparse_whole.pk))}
    mesh_whole = await models.MeshLens.objects.aget(mesh_collection_id=mesh_id)
    network_whole = await models.NetworkLens.objects.aget(network_collection_id=network_id)
    assert await found("v1") == {("MESH", str(mesh_whole.pk)), ("NETWORK", str(network_whole.pk))}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_of_a_window_is_the_tables_lens_for_the_tighter_bounds(authenticated_context: HttpContext):
    """Windows stated over a lens intersect with its own; an empty intersection is refused."""
    ctx = authenticated_context
    table_id, mutation, field = await _a_table(ctx)
    outer = await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 2.0, "max": 8.0}])
    inner = await _create(ctx, mutation, "lens", outer["id"], [{"axis": "t", "min": 5.0, "max": 20.0}])
    direct = await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 5.0, "max": 8.0}])
    assert inner["id"] == direct["id"], "t 5..8, whichever way it was asked for"
    assert inner["id"] != outer["id"]

    refused = await _create(ctx, mutation, "lens", outer["id"], [{"axis": "t", "min": 9.0}])
    assert isinstance(refused, list) and "keeps nothing" in refused[0].message
    both = await schema.execute(f"mutation M($input: {mutation}Input!) {{ {mutation[0].lower() + mutation[1:]}(input: $input) {{ id }} }}", context_value=ctx, variable_values={"input": {field: table_id, "lens": outer["id"], "windows": []}})
    assert both.errors and "exactly one" in both.errors[0].message.lower()


_CONTAINER_FIELD = {"TABLE": "tableDataset", "SPARSE": "sparseDataset", "MESH": "meshCollection", "NETWORK": "networkCollection", "ANNOTATION": "annotationCollection"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_a_container_of_any_kind_lists_its_lenses_and_names_its_whole_one(authenticated_context: HttpContext, kind: str):
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)
    window = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])
    whole = await model.objects.aget(**{model.CONTAINER_FIELD + "_id": container_id}, windows=[])

    query = f"query C($id: ID!) {{ {_CONTAINER_FIELD[kind]}(id: $id) {{ fullLens {{ id kind }} lenses {{ id }} windowed: lenses(windowed: true) {{ id }} }} }}"
    result = await schema.execute(query, context_value=ctx, variable_values={"id": container_id})
    assert not result.errors, result.errors
    container = result.data[_CONTAINER_FIELD[kind]]
    assert container["fullLens"] == {"id": str(whole.pk), "kind": kind}
    assert [lens["id"] for lens in container["lenses"]] == [str(whole.pk), window["id"]], "oldest first: the whole lens it was born with, then the window"
    assert [lens["id"] for lens in container["windowed"]] == [window["id"]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_whole_lens_over_a_table_nominates_a_scene_of_its_own_and_lists_the_scenes_that_show_it(authenticated_context: HttpContext):
    """A table nominates no scene, so its whole lens holds the nomination itself; `scenes` and `scenes(filters: {lens})` answer alike for any kind."""
    ctx = authenticated_context
    table, _ = await _seed_table_dataset(ctx, "spots")
    whole = await seed.create_windowed_lens(ctx, table)
    staged = await schema.execute("mutation F($input: CreateSceneFromLensInput!) { createSceneFromLens(input: $input) { id } }", context_value=ctx, variable_values={"input": {"lens": str(whole.pk)}})
    assert not staged.errors, staged.errors
    scene_id = staged.data["createSceneFromLens"]["id"]

    read = await schema.execute(
        "query L($id: ID!) { lens(id: $id) { defaultScene { id } scenes { id } } scenes(filters: { lens: $id }) { id } }",
        context_value=ctx,
        variable_values={"id": str(whole.pk)},
    )
    assert not read.errors, read.errors
    assert read.data["lens"]["defaultScene"] == {"id": scene_id}, "staging nominated the new scene on the lens itself"
    assert read.data["lens"]["scenes"] == read.data["scenes"] == [{"id": scene_id}]

    cleared = await schema.execute("mutation S($input: SetLensDefaultSceneInput!) { setLensDefaultScene(input: $input) { defaultScene { id } } }", context_value=ctx, variable_values={"input": {"lens": str(whole.pk), "scene": None}})
    assert not cleared.errors, cleared.errors
    assert cleared.data["setLensDefaultScene"]["defaultScene"] is None, "nothing to fall back on: a table nominates no scene"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_window_may_be_a_single_position(authenticated_context: HttpContext):
    """Inclusive on both sides, `[5, 5]` keeps the positions at 5 -- one timepoint, one feature, one channel -- and is a lens like any other."""
    ctx = authenticated_context
    table_id, mutation, field = await _a_table(ctx)
    plane = await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 5, "max": 5}])
    assert isinstance(plane, dict), plane
    read = await _read(ctx, plane["id"])
    assert read["windows"] == [{"axis": "t", "min": 5.0, "max": 5.0}]
    assert (await _create(ctx, mutation, field, table_id, [{"axis": "t", "min": 5.0, "max": 5.0}]))["id"] == plane["id"], "and the same position is the same lens"
