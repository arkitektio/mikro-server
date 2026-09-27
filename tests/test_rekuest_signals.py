"""mikro announces new array datasets to the hub's rekuest (vendored ``rekuest_service``).

The signal goes to a real HTTP server on a local port, standing in for rekuest's intake; what it
receives is checked the way rekuest checks it: the V1 signature over ``signal:mikro``, the body,
and the raw provenance token of the task the dataset was created in.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from datalayer.models import ZarrStore
from kante.context import HttpContext

from core.descriptors import array_descriptors
from mikro_server.schema import schema
from joserfc.jwk import OKPKey

from rekuest_service import trust
from tests.test_task_provenance import attach_provenance, clear_provenance, make_provenance

MIKRO_KEY = OKPKey.generate_key("Ed25519")
REKUEST_KEY = OKPKey.generate_key("Ed25519")
INSTANCE = {
    "PRIVATE_KEY": MIKRO_KEY.as_pem(private=True).decode(),
    "TRUST_JWKS": {
        "keys": [
            {**trust.public_jwk(MIKRO_KEY), "service": "live.arkitekt.mikro"},
            {**trust.public_jwk(REKUEST_KEY), "service": "live.arkitekt.rekuest"},
        ]
    },
}


class _Intake:
    """A local HTTP server recording every POST, as rekuest's signal intake would receive it."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        intake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                intake.received.append({"path": self.path, "headers": dict(self.headers), "body": body})
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args):  # quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def wait(self, count: int = 1, timeout: float = 10) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.received


def _dataset_created(intake) -> dict:
    """The dataset's CREATED signal. Creating one also signals its folder, and the mutation's
    later saves of the row are UPDATEDs — only the CREATED is the one these tests are about."""
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for received in intake.received:
            body = json.loads(received["body"])
            if body["identifier"] == "@mikro/arraydataset" and body["kind"] == "CREATED":
                return received
        time.sleep(0.05)
    raise AssertionError(f"no @mikro/arraydataset CREATED among {[json.loads(r['body'])['identifier'] for r in intake.received]}")


@pytest.fixture
def intake(settings):
    server = _Intake()
    settings.REKUEST_HOOK = {"REKUEST_URL": server.url, "SERVICE": "mikro"}
    settings.INSTANCE = INSTANCE
    yield server
    server.server.shutdown()


def test_descriptors_follow_the_client_vocabulary():
    assert array_descriptors(["TIME", "CHANNEL", "SPACE", "SPACE", "SPACE"], [10, 3, 5, 64, 64]) == {
        "@mikro/n_space_axes": 3,
        "@mikro/n_time_axes": 1,
        "@mikro/n_channel_axes": 1,
        "@mikro/n_spectrum_axes": 0,
        "@mikro/n_microtime_axes": 0,
        "@mikro/n_channels": 3,
        "@mikro/n_timepoints": 10,
    }
    with pytest.raises(ValueError):
        array_descriptors(["SPACE"], [1, 2])


CREATE = """
    mutation Create($input: CreateArrayDatasetInput!) { createArrayDataset(input: $input) { id } }
"""


async def _create(ctx: HttpContext, name: str) -> str:
    store = await ZarrStore.objects.acreate(organization=ctx.request.organization, key=name, bucket="zarr", shape=[2, 32, 32], chunks=[2, 32, 32], version="3", dtype="uint8", populated=True)
    with patch("datalayer.models.ZarrStore.fill_info", return_value=None):
        result = await schema.execute(
            CREATE,
            context_value=ctx,
            variable_values={"input": {"name": name, "data": str(store.id), "scales": [], "axes": [{"name": "c", "type": "CHANNEL"}, {"name": "y", "type": "SPACE"}, {"name": "x", "type": "SPACE"}]}},
        )
    assert not result.errors, result.errors
    return result.data["createArrayDataset"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_created_dataset_is_signalled_with_its_task_token(intake, authenticated_context: HttpContext):
    attach_provenance(authenticated_context, make_provenance(task_id="57"))
    dataset_id = await _create(authenticated_context, "signalled")
    clear_provenance(authenticated_context)

    received = _dataset_created(intake)
    assert received["path"] == "/agi/signal/mikro"
    # Checked the way rekuest checks it: signed with mikro's instance key, for rekuest, for this path and body.
    verified = trust.verify("POST", received["path"], received["body"], received["headers"]["Authorization"], audience="live.arkitekt.rekuest")
    assert verified.issuer == "live.arkitekt.mikro"
    body = json.loads(received["body"])
    assert (body["kind"], body["identifier"], body["object"]) == ("CREATED", "@mikro/arraydataset", dataset_id)
    assert body["organization"] == authenticated_context.request.organization.slug
    assert body["descriptors"]["@mikro/n_channels"] == 2 and body["descriptors"]["@mikro/n_space_axes"] == 2
    assert body["provenance"] == "raw-provenance-token"  # forwarded untouched; rekuest verifies it


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_without_a_task_the_signal_carries_no_token(intake, authenticated_context: HttpContext):
    await _create(authenticated_context, "untasked")
    received = _dataset_created(intake)
    assert json.loads(received["body"])["provenance"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nothing_is_sent_when_rekuest_hook_is_not_configured(intake, settings, authenticated_context: HttpContext):
    settings.REKUEST_HOOK = None
    await _create(authenticated_context, "unconfigured")
    time.sleep(0.5)
    assert intake.received == []


def test_the_manifest_declares_exactly_the_keys_mikro_sends(settings):
    from django.test import Client as HttpClient
    from django.urls import reverse

    settings.REKUEST_HOOK = {"REKUEST_URL": "http://127.0.0.1:9", "SERVICE": "mikro"}
    settings.INSTANCE = INSTANCE
    url = reverse("rekuest_hook_manifest")  # loads the URLconf, which registers the declarations
    authorization = trust.sign("GET", url, b"", issuer="live.arkitekt.rekuest", audience="live.arkitekt.mikro", key=REKUEST_KEY)
    response = HttpClient().get(url, headers={"Authorization": authorization, "X-Rekuest-Agent": "1"})
    (declared,) = [s for s in response.json()["signals"] if s["identifier"] == "@mikro/arraydataset"]
    assert declared["kinds"] == ["CREATED", "UPDATED", "DELETED"]
    assert sorted(declared["descriptors"]) == sorted(array_descriptors(["CHANNEL", "SPACE"], [2, 8]))


@pytest.mark.django_db(transaction=True)
def test_an_undeclared_signal_warns_but_is_still_sent(intake, caplog):
    from rekuest_service.signals import emit

    with caplog.at_level("WARNING", logger="rekuest_service"):
        emit("DELETED", "@mikro/arraydataset", 7, organization="org")
    assert "without declaring it" in caplog.text
    (received,) = intake.wait()
    assert json.loads(received["body"])["kind"] == "DELETED"
