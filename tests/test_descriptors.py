"""``descriptors`` on mikro's types: what an object says about itself, from its structure's declaration.

One declaration (``mikro_server.service``) feeds the manifest, the signals and this field, so the
tests hold the three to each other: the field answers what a signal about the object carries, in
the keys the manifest declares.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from authentikate.models import Organization
from kante.context import HttpContext

from core import enums, models
from embeddings.healer import stale_queryset
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
    }
    assert result.data["arrayDataset"]["descriptors"] == whole
    assert {"id": str(dataset.pk), "descriptors": whole} in result.data["arrayDatasets"]
    # The lens keeps every axis of its dataset and narrows the channel axis to one.
    assert result.data["lens"]["descriptors"] == {**whole, "@mikro/n_channels": 1}
    # A structure that declares no descriptors has none.
    assert result.data["folder"]["descriptors"] == {}

    declared = {s["identifier"]: [d["key"] for d in s["descriptors"]] for s in service.manifest()["structures"]}
    assert set(result.data["arrayDataset"]["descriptors"]) == set(declared["@mikro/arraydataset"])
    assert set(result.data["lens"]["descriptors"]) == set(declared["@mikro/lens"])


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
async def test_a_sweep_for_one_organization_claims_only_its_rows(authenticated_context: HttpContext):
    """Every organization has the agent and its own schedule: a run must not do another's work."""
    mine = await seed.create_folder(authenticated_context, "mine")
    elsewhere = await Organization.objects.acreate(slug="elsewhere")
    theirs = await models.Folder.objects.acreate(
        name="theirs", creator=authenticated_context.request.user, organization=elsewhere, membership=authenticated_context.request.membership
    )
    await models.Folder.objects.filter(pk__in=[mine.pk, theirs.pk]).aupdate(embedding_model="another-model")

    def stale(organization: str | None) -> set[int]:
        return set(stale_queryset(models.Folder, organization).filter(pk__in=[mine.pk, theirs.pk]).values_list("pk", flat=True))

    assert await sync_to_async(stale)("elsewhere") == {theirs.pk}
    assert await sync_to_async(stale)(authenticated_context.request.organization.slug) == {mine.pk}
    assert await sync_to_async(stale)(None) == {mine.pk, theirs.pk}
