"""mikro's model signals: what it declares to the hub's rekuest, and what a save sends.

Each save goes to a real local HTTP server standing in for rekuest's signal intake (from
``test_rekuest_signals``), checked the way rekuest checks it.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from mikro_server.service import service
from arkitekt_service import trust
from tests import seed
from tests.test_rekuest_signals import intake  # noqa: F401  the fixture
from tests.test_scene_snapshot import _snapshot
from tests.test_task_provenance import attach_provenance, clear_provenance, make_provenance
from tests.test_windowed_lenses import _CONTAINERS, _create

EXPECTED = {
    "@mikro/arraylens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/tablelens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/sparselens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/meshlens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/networklens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/annotationlens": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/arraydataset": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/scene": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/chart": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/scenesnapshot": ["CREATED", "DELETED"],
    "@mikro/tabledataset": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/meshcollection": ["CREATED", "DELETED"],
    "@mikro/networkcollection": ["CREATED", "DELETED"],
    "@mikro/file": ["CREATED", "DELETED"],
    "@mikro/folder": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/annotationcollection": ["CREATED", "DELETED"],
    "@mikro/animation": ["CREATED", "DELETED"],
    "@mikro/sparsedataset": ["CREATED", "DELETED"],
}


def _of(intake, identifier: str, count: int = 1) -> list[dict]:  # noqa: F811
    import time

    def matching() -> list[dict]:
        rows = [{**r, "json": json.loads(r["body"])} for r in intake.received]
        return [r for r in rows if r["json"]["identifier"] == identifier]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if len(found := matching()) >= count:
            return found
        time.sleep(0.05)
    return matching()


def test_the_manifest_declares_every_model_signal():
    declared = {s["identifier"]: s["kinds"] for s in service.manifest()["signals"]}
    assert declared == EXPECTED


def test_the_manifest_lists_what_mikro_hosts_with_its_descriptors():
    hosted = {s["identifier"]: s for s in service.manifest()["structures"]}
    # Everything hosted is signalled, lenses included: nothing lives here silently.
    assert set(hosted) == set(EXPECTED)
    assert hosted["@mikro/folder"]["label"] == "Folder"
    array_keys = [d["key"] for d in hosted["@mikro/arraydataset"]["descriptors"]]
    assert array_keys == [d["key"] for d in hosted["@mikro/arraylens"]["descriptors"]]
    assert {"key": "@mikro/n_channels", "type": "INT", "description": "Its total extent along its CHANNEL axes"} in hosted["@mikro/arraydataset"]["descriptors"]
    # A container and the lens kind over it read one vocabulary.
    assert [d["key"] for d in hosted["@mikro/tabledataset"]["descriptors"]] == [d["key"] for d in hosted["@mikro/tablelens"]["descriptors"]] != []
    assert hosted["@mikro/folder"]["descriptors"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_scene_is_signalled_with_its_descriptors(intake, authenticated_context: HttpContext):  # noqa: F811
    scene = await seed.create_scene(authenticated_context, "Signalled scene")

    (received,) = _of(intake, "@mikro/scene")
    body = received["json"]
    assert (body["kind"], body["object"]) == ("CREATED", str(scene.pk))
    assert body["organization"] == authenticated_context.request.organization.slug
    assert set(body["descriptors"]) == {"@mikro/blending", "@mikro/preferred_view"}
    assert trust.verify("POST", received["path"], received["body"], received["headers"]["Authorization"], audience="live.arkitekt.rekuest").issuer == "live.arkitekt.mikro"

    await sync_to_async(lambda: models.Scene.objects.get(pk=scene.pk).delete())()
    kinds = [r["json"]["kind"] for r in _of(intake, "@mikro/scene", 2)]
    assert kinds == ["CREATED", "DELETED"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_snapshot_made_in_a_task_carries_that_tasks_token(intake, authenticated_context: HttpContext):  # noqa: F811
    scene = await seed.create_scene(authenticated_context, "Rendered")
    attach_provenance(authenticated_context, make_provenance(task_id="88"))
    snapshot = await _snapshot(authenticated_context, scene, "render.png")
    clear_provenance(authenticated_context)

    (received,) = _of(intake, "@mikro/scenesnapshot")
    body = received["json"]
    assert (body["kind"], body["object"]) == ("CREATED", str(snapshot["id"]))
    assert body["provenance"] == "raw-provenance-token"  # rekuest makes triggered runs children of task 88
    assert json.loads(received["body"])["identifier"] == "@mikro/scenesnapshot"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_crop_is_signalled_with_its_descriptors(intake, authenticated_context: HttpContext):  # noqa: F811
    """The whole lens arrives with its dataset; a crop on its own, saying it is not whole; a rename and a delete follow it."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Signalled", axes=seed.SIMPLE_AXES, shapes=[[3, 64, 64]])
    crop = await seed.create_lens(ctx, dataset, slices=[{"axis": "c", "start": 0, "stop": 1}])

    whole_created, crop_created = [r["json"] for r in _of(intake, "@mikro/arraylens", 2)]
    assert (whole_created["kind"], whole_created["descriptors"]["@mikro/is_whole"], whole_created["descriptors"]["@mikro/n_channels"]) == ("CREATED", True, 3)
    assert (crop_created["kind"], crop_created["object"], crop_created["descriptors"]["@mikro/is_whole"], crop_created["descriptors"]["@mikro/n_channels"]) == ("CREATED", str(crop.pk), False, 1)
    row = await models.Lens.objects.with_sources().aget(pk=crop.pk)
    assert crop_created["descriptors"] == await sync_to_async(service.describe)(row), "the signal carries what the field answers"

    rename = "mutation R($input: UpdateLensInput!) { updateLens(input: $input) { id } }"
    result = await schema.execute(rename, context_value=ctx, variable_values={"input": {"id": str(crop.pk), "name": "nucleus"}})
    assert not result.errors, result.errors
    delete = "mutation D($input: DeleteLensInput!) { deleteLens(input: $input) }"
    result = await schema.execute(delete, context_value=ctx, variable_values={"input": {"id": str(crop.pk)}})
    assert not result.errors, result.errors

    about_crop = [r["json"]["kind"] for r in _of(intake, "@mikro/arraylens", 4) if r["json"]["object"] == str(crop.pk)]
    assert about_crop == ["CREATED", "UPDATED", "DELETED"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_CONTAINERS))
async def test_a_windowed_lens_is_signalled_and_goes_with_its_container(intake, authenticated_context: HttpContext, kind: str):  # noqa: F811
    """A window is announced in its kind's vocabulary; deleting the container announces the lens gone, described while its container still stands."""
    ctx = authenticated_context
    make, model, axis = _CONTAINERS[kind]
    container_id, mutation, field = await make(ctx)
    identifier = f"@mikro/{model.__name__.lower()}"
    windowed = await _create(ctx, mutation, field, container_id, [{"axis": axis, "min": 0, "max": 5}])

    created = [r["json"] for r in _of(intake, identifier, 2)]
    by_object = {body["object"]: body for body in created}
    assert by_object[windowed["id"]]["kind"] == "CREATED" and by_object[windowed["id"]]["descriptors"]["@mikro/is_whole"] is False
    assert any(body["descriptors"]["@mikro/is_whole"] is True for body in created), "the whole lens arrived with its container"

    container = await sync_to_async(lambda: models.Lens.objects.get(pk=windowed["id"]).container)()
    await sync_to_async(container.delete)()
    gone = [r["json"] for r in _of(intake, identifier, 4) if r["json"]["kind"] == "DELETED"]
    assert {body["object"] for body in gone} == {body["object"] for body in created}, "the whole lens and the window both went with the container"
    assert all(body["descriptors"] != {} for body in gone), "described at delete time, before the cascade reaches the container"
