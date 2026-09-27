"""mikro's model signals: what it declares to the hub's rekuest, and what a save sends.

Each save goes to a real local HTTP server standing in for rekuest's signal intake (from
``test_rekuest_signals``), checked the way rekuest checks it.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from core import models
from mikro_server.service import service
from rekuest_service import trust
from tests import seed
from tests.test_rekuest_signals import intake  # noqa: F401  the fixture
from tests.test_scene_snapshot import _snapshot
from tests.test_task_provenance import attach_provenance, clear_provenance, make_provenance

EXPECTED = {
    "@mikro/arraydataset": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/scene": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/scenesnapshot": ["CREATED", "DELETED"],
    "@mikro/tabledataset": ["CREATED", "UPDATED", "DELETED"],
    "@mikro/meshcollection": ["CREATED", "DELETED"],
    "@mikro/file": ["CREATED", "DELETED"],
    "@mikro/dataset": ["CREATED", "UPDATED", "DELETED"],
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
