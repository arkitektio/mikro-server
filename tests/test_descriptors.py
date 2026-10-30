"""``descriptors`` on mikro's types: what an object says about itself, from its structure's declaration.

One declaration (``mikro_server.service``) feeds the manifest, the signals and this field, so the
tests hold the three to each other: the field answers what a signal about the object carries, in
the keys the manifest declares.
"""

import json

import pytest
from kante.context import HttpContext

from core import enums
from mikro_server.schema import schema
from mikro_server.service import service
from tests import seed
from tests.test_rekuest_signals import intake  # noqa: F401  the fixture
from tests.test_signals import _of

DESCRIBED = """
    query Described($dataset: ID!, $lens: ID!, $folder: ID!) {
        arrayDataset(id: $dataset) { descriptors }
        lens(id: $lens) { descriptors }
        folder(id: $folder) { descriptors }
        arrayDatasets { id descriptors }
    }
"""

AXES = [seed.axis("t", enums.AxisType.TIME), seed.axis("c", enums.AxisType.CHANNEL), seed.axis("y", enums.AxisType.SPACE), seed.axis("x", enums.AxisType.SPACE)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_object_answers_the_descriptors_its_structure_declares(authenticated_context: HttpContext):
    dataset = await seed.create_array_dataset(authenticated_context, "timelapse", axes=AXES, shapes=[[5, 3, 64, 64]])
    lens = await seed.create_lens(authenticated_context, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}])
    folder = await seed.create_folder(authenticated_context, "shelf")

    result = await schema.execute(DESCRIBED, context_value=authenticated_context, variable_values={"dataset": str(dataset.pk), "lens": str(lens.pk), "folder": str(folder.pk)})
    assert not result.errors, result.errors

    whole = {
        "@mikro/n_space_axes": 2,
        "@mikro/n_time_axes": 1,
        "@mikro/n_channel_axes": 1,
        "@mikro/n_spectrum_axes": 0,
        "@mikro/n_microtime_axes": 0,
        "@mikro/n_channels": 3,
        "@mikro/n_timepoints": 5,
        "@mikro/size": 61440,
        "@mikro/value_kind": "intensity",
        # Seeded without a zarr store, so with no element type to state, and no @mikro/dsize.
        "@mikro/dtype": "",
        "@mikro/spec": ["IMAGE", "TIMESERIES", "MULTICHANNEL"],
        "@mikro/is_whole": True,
    }
    assert result.data["arrayDataset"]["descriptors"] == whole
    assert {"id": str(dataset.pk), "descriptors": whole} in result.data["arrayDatasets"]
    # The lens keeps every axis of its dataset and narrows the channel axis to one; it is a crop.
    # Its spec is the selection's: one channel is not MULTICHANNEL.
    assert result.data["lens"]["descriptors"] == {**whole, "@mikro/n_channels": 1, "@mikro/size": 20480, "@mikro/spec": ["IMAGE", "TIMESERIES"], "@mikro/is_whole": False}
    # A structure that declares no descriptors has none.
    assert result.data["folder"]["descriptors"] == {}

    declared = {s["identifier"]: [d["key"] for d in s["descriptors"]] for s in service.manifest()["structures"]}
    # Every computed key is declared and every declared key is computed: nothing is merely stated.
    assert set(result.data["arrayDataset"]["descriptors"]) | {"@mikro/dsize"} == set(declared["@mikro/arraydataset"])
    assert set(result.data["lens"]["descriptors"]) | {"@mikro/dsize"} == set(declared["@mikro/arraylens"])

    # A formula's frontend asks for the same answer by identifier, and only inside the organization.
    asked = await schema.execute("query Ask($id: ID!) { describe(identifier: \"@mikro/arraydataset\", id: $id) { key value } }", context_value=authenticated_context, variable_values={"id": str(dataset.pk)})
    assert not asked.errors, asked.errors
    assert {each["key"]: each["value"] for each in asked.data["describe"]} == whole


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_field_answers_what_the_signal_carried(intake, authenticated_context: HttpContext):  # noqa: F811
    scene = await seed.create_scene(authenticated_context, "Described scene")

    (received,) = _of(intake, "@mikro/scene")
    result = await schema.execute("query Scene($id: ID!) { scene(id: $id) { descriptors } }", context_value=authenticated_context, variable_values={"id": str(scene.pk)})
    assert not result.errors, result.errors
    assert result.data["scene"]["descriptors"] == json.loads(received["body"])["descriptors"] != {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_annotation_says_what_shape_it_is_and_the_signal_carries_the_same(intake, authenticated_context: HttpContext):  # noqa: F811
    scene = await seed.create_scene(authenticated_context, "Canvas")
    drawn = await schema.execute(
        "mutation Draw($input: CreateAnnotationInput!) { createAnnotation(input: $input) { id descriptors } }",
        context_value=authenticated_context,
        variable_values={"input": {"scene": str(scene.pk), "kind": "POINT", "vectors": [[1.0, 2.0, 3.0]]}},
    )
    assert not drawn.errors, drawn.errors
    described = drawn.data["createAnnotation"]["descriptors"]

    # The kind in the schema's spelling, so a port requires it as it would write the enum.
    assert described["@mikro/annotation_kind"] == "POINT"
    declared = {s["identifier"]: [d["key"] for d in s["descriptors"]] for s in service.manifest()["structures"]}
    assert set(described) == set(declared["@mikro/annotation"])

    (received,) = _of(intake, "@mikro/annotation")
    body = json.loads(received["body"])
    assert (body["kind"], body["object"]) == ("CREATED", drawn.data["createAnnotation"]["id"])
    assert body["descriptors"] == described
    assert body["organization"] == authenticated_context.request.organization.identifier

