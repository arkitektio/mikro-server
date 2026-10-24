"""A fixture: one kulisse model (a self-contained binary glTF), living in a unit-carrying space of its own.

What is on trial is the part of a model that is not a file. Its space carries the unit the
exporter wrote, so an IDENTITY registration into a micrometre world comes out a thousand
times larger for a millimetre model without anyone stating the factor; its box is the one
its kulisse store read off the file rather than one declared; and a layer over it obeys the same placement gate every other
layer does.
"""

import pytest
from kante.context import HttpContext

from core import models
from arkitekt_datalayer.models import KulisseStore
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request

CREATE_MODEL = """
mutation Create($input: CreateFixtureInput!) {
  createFixture(input: $input) {
    id
    label
    bounds
    fullLens { id }
    coordinateSystem { id axes { name unit } }
  }
}
"""

CREATE_LAYER = """
mutation Create($input: CreateFixtureLayerInput!) {
  createFixtureLayer(input: $input) {
    id
    materialColor
    wireframe
    fixture { id }
    asAffine { matrix }
  }
}
"""

UPDATE_LAYER = """
mutation Update($input: UpdateFixtureLayerInput!) {
  updateFixtureLayer(input: $input) { id materialColor wireframe opacity }
}
"""

REGISTER = """
mutation Create($input: CreateTransformationInput!) {
  createTransformation(input: $input) { id }
}
"""

MM_AXES = [{"name": axis, "type": "SPACE", "unit": "millimeter"} for axis in ("x", "y", "z")]


#: What a kulisse store read off a 24 x 24 x 0.17 mm coverslip moved 10 mm along x: the box is
#: computed by the datalayer's reader when the upload is finished, never here.
COVERSLIP_BOUNDS = {"min": [-2.0, -12.0, 0.0], "max": [22.0, 12.0, 0.17]}


async def _kulisse_store(ctx: HttpContext, key: str = "coverslip.glb", populated: bool = True) -> KulisseStore:
    return await KulisseStore.objects.acreate(
        organization=ctx.request.organization,
        path=f"s3://kulisse/{key}",
        key=key,
        bucket="kulisse",
        content_type="model/gltf-binary",
        original_file_name=key,
        populated=populated,
        bounds=COVERSLIP_BOUNDS if populated else None,
        nodes=["coverslip"] if populated else None,
        joints=[] if populated else None,
    )


async def _register(ctx: HttpContext, system_id: str, scene: models.Scene) -> None:
    """Place the model's space in the scene's world by name: x onto x, y onto y, z onto z, converted by the units."""
    same = {"kind": "IDENTITY", "inputAxes": ["x", "y", "z"], "outputAxes": ["x", "y", "z"]}
    result = await schema.execute(
        REGISTER,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"input": system_id, "output": str(scene.world_id), "transform": {"kind": "BY_DIMENSION", "parts": [same]}}},
    )
    assert not result.errors, result.errors


async def _create_model(ctx: HttpContext, populated: bool = True, **extra: object):
    store = await _kulisse_store(ctx, populated=populated)
    return await schema.execute(CREATE_MODEL, context_value=ctx, variable_values={"input": {"store": str(store.pk), "axes": MM_AXES, **extra}})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_model_lives_in_a_space_carrying_its_units(authenticated_context: HttpContext):
    result = await _create_model(authenticated_context, name="Coverslip")
    assert not result.errors, result.errors

    created = result.data["createFixture"]
    assert created["label"] == "Coverslip"
    assert created["bounds"] == {"min": [-2.0, -12.0, 0.0], "max": [22.0, 12.0, 0.17]}
    assert created["fullLens"] is not None
    assert [(axis["name"], axis["unit"]) for axis in created["coordinateSystem"]["axes"]] == [("x", "millimeter"), ("y", "millimeter"), ("z", "millimeter")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_store_whose_upload_was_not_finished_is_refused(authenticated_context: HttpContext):
    """A kulisse store is read and checked when its upload is finished; one that was not may hold anything."""
    result = await _create_model(authenticated_context, populated=False)
    assert result.errors and "has not been finished" in result.errors[0].message
    assert await models.Fixture.objects.acount() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_three_spatial_axes_are_a_model_space(authenticated_context: HttpContext):
    result = await _create_model(authenticated_context, axes=[*MM_AXES[:2], {"name": "t", "type": "TIME", "unit": "second"}])
    assert result.errors and "not SPACE axes" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_identity_registration_converts_by_the_units(authenticated_context: HttpContext):
    """Millimetres into a micrometre world: a factor of 1000 nobody stated."""
    created = (await _create_model(authenticated_context)).data["createFixture"]
    scene = await seed.create_scene(authenticated_context, "Stage")
    await _register(authenticated_context, created["coordinateSystem"]["id"], scene)

    result = await schema.execute(CREATE_LAYER, context_value=_fresh_request(authenticated_context), variable_values={"input": {"scene": str(scene.pk), "lens": created["fullLens"]["id"]}})
    assert not result.errors, result.errors
    layer = result.data["createFixtureLayer"]
    assert layer["materialColor"] is None, "a fixture draws its own materials unless told otherwise"
    assert layer["fixture"]["id"] == created["id"]
    # The world is (z, y, x) and the model (x, y, z): the map reverses them, and scales each by 1000.
    matrix = layer["asAffine"]["matrix"]
    assert [[round(value, 6) for value in row[:3]] for row in matrix[:3]] == [[0.0, 0.0, 1000.0], [0.0, 1000.0, 0.0], [1000.0, 0.0, 0.0]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_layer_needs_a_path_into_the_world(authenticated_context: HttpContext):
    created = (await _create_model(authenticated_context)).data["createFixture"]
    scene = await seed.create_scene(authenticated_context, "Unregistered")
    result = await schema.execute(CREATE_LAYER, context_value=authenticated_context, variable_values={"input": {"scene": str(scene.pk), "lens": created["fullLens"]["id"]}})
    assert result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clearing_the_colour_returns_to_the_models_materials(authenticated_context: HttpContext):
    created = (await _create_model(authenticated_context)).data["createFixture"]
    scene = await seed.create_scene(authenticated_context, "Stage")
    await _register(authenticated_context, created["coordinateSystem"]["id"], scene)
    layer = (await schema.execute(CREATE_LAYER, context_value=_fresh_request(authenticated_context), variable_values={"input": {"scene": str(scene.pk), "lens": created["fullLens"]["id"], "materialColor": [200, 220, 255, 255]}})).data["createFixtureLayer"]

    kept = await schema.execute(UPDATE_LAYER, context_value=authenticated_context, variable_values={"input": {"id": layer["id"], "wireframe": True}})
    assert kept.data["updateFixtureLayer"]["materialColor"] == [200, 220, 255, 255], "an omitted colour is kept"

    cleared = await schema.execute(UPDATE_LAYER, context_value=authenticated_context, variable_values={"input": {"id": layer["id"], "materialColor": None}})
    assert cleared.data["updateFixtureLayer"]["materialColor"] is None
    assert cleared.data["updateFixtureLayer"]["wireframe"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_clip_box_is_a_lens_in_the_models_own_units(authenticated_context: HttpContext):
    """A window over a fixture is stated in its axes and unit, and asked again finds the same lens."""
    created = (await _create_model(authenticated_context)).data["createFixture"]
    mutation = "mutation Cut($input: CreateFixtureLensInput!) { createFixtureLens(input: $input) { id windows { axis min max } fixture { id } } }"
    variables = {"input": {"fixture": created["id"], "windows": [{"axis": "x", "min": 0.0, "max": 5.0}]}}

    first = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert not first.errors, first.errors
    lens = first.data["createFixtureLens"]
    assert lens["fixture"]["id"] == created["id"]
    assert lens["windows"] == [{"axis": "x", "min": 0.0, "max": 5.0}]
    assert lens["id"] != created["fullLens"]["id"]

    again = await schema.execute(mutation, context_value=authenticated_context, variable_values=variables)
    assert again.data["createFixtureLens"]["id"] == lens["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_model_is_a_resident_of_its_space(authenticated_context: HttpContext):
    """Its space answers `residents` with the model, and not with its whole lens beside it."""
    created = (await _create_model(authenticated_context, name="Coverslip")).data["createFixture"]
    result = await schema.execute(
        "query Space($id: ID!) { coordinateSystem(id: $id) { residents { __typename ... on Fixture { id } } } }",
        context_value=authenticated_context,
        variable_values={"id": created["coordinateSystem"]["id"]},
    )
    assert not result.errors, result.errors
    assert result.data["coordinateSystem"]["residents"] == [{"__typename": "Fixture", "id": created["id"]}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_model_is_filed_and_listed_by_its_files_name(authenticated_context: HttpContext):
    """Unnamed, a model sorts and searches by the name of the file it was uploaded as."""
    folder = await seed.create_folder(authenticated_context, "Hardware")
    created = (await _create_model(authenticated_context, folder=str(folder.pk))).data["createFixture"]
    assert created["label"] == "coverslip.glb"

    query = "query Children($parent: ID!, $filters: FolderChildrenFilter, $order: ChildrenOrder) { children(parent: $parent, filters: $filters, order: $order) { __typename ... on Fixture { id label } } }"
    listed = await schema.execute(query, context_value=authenticated_context, variable_values={"parent": str(folder.pk), "order": {"field": "NAME", "direction": "ASC"}})
    assert not listed.errors, listed.errors
    assert listed.data["children"] == [{"__typename": "Fixture", "id": created["id"], "label": "coverslip.glb"}]
