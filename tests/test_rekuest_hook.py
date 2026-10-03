"""This service's HookAgent as seen by the hub's rekuest (vendored ``rekuest_service``).

The manifest rekuest reads, the signed requests both sides exchange (instance keys vouched for
by the hub's trust bundle, no shared secret), and the ``reembed_stale`` action itself against
the real database.
"""

import json

import pytest
from django.test import Client as HttpClient
from django.urls import reverse
from joserfc.jwk import OKPKey

from rekuest_service import trust
from mikro_server.service import agent, service

REKUEST_KEY = OKPKey.generate_key("Ed25519")
SERVICE_KEY = OKPKey.generate_key("Ed25519")


@pytest.fixture
def hooked(settings):
    settings.REKUEST_HOOK = {"REKUEST_URL": "http://127.0.0.1:9", "MAX_SKEW": 30}
    settings.INSTANCE = {
        "PRIVATE_KEY": SERVICE_KEY.as_pem(private=True).decode(),
        "TRUST_JWKS": {
            "keys": [
                {**trust.public_jwk(SERVICE_KEY), "service": service.identifier},
                {**trust.public_jwk(REKUEST_KEY), "service": "live.arkitekt.rekuest"},
            ]
        },
    }
    return settings


def _as_rekuest(method: str, url: str, body: bytes = b"", *, key=REKUEST_KEY, audience=None) -> dict:
    authorization = trust.sign(method, url, body, issuer="live.arkitekt.rekuest", audience=audience or service.identifier, key=key)
    return {"Authorization": authorization, "X-Rekuest-Agent": "11"}


def test_the_manifest_is_signed_and_lists_reembed_stale(hooked):
    client = HttpClient()
    url = reverse("rekuest_hook_manifest")
    assert client.get(url).status_code == 401
    response = client.get(url, headers=_as_rekuest("GET", url))
    assert response.status_code == 200
    assert "reembed_stale" in [a["interface"] for a in response.json()["actions"]]


def test_forged_and_unconfigured_requests_are_refused(hooked, settings):
    client = HttpClient()
    url = reverse("rekuest_hook")
    body = json.dumps({"type": "ASSIGN", "task": "1", "interface": "reembed_stale", "args": {}}).encode()

    def post(headers):
        return client.post(url, data=body, content_type="application/json", headers=headers).status_code

    assert post(_as_rekuest("POST", url, body, key=SERVICE_KEY)) == 401  # this service's own key, posing as rekuest
    assert post(_as_rekuest("POST", url, body, audience="live.arkitekt.other")) == 401  # rekuest's, but for another service
    assert post(_as_rekuest("POST", url, body, key=OKPKey.generate_key("Ed25519"))) == 401  # a key nobody vouched for
    settings.INSTANCE = None
    assert post(_as_rekuest("POST", url, body)) == 503


@pytest.mark.django_db
def test_reembed_stale_runs_one_bounded_pass():
    result = agent.actions["reembed_stale"].function(organization="nobody")
    assert result == {"reembedded": 0}


def test_the_service_itself_offers_no_actions():
    assert not hasattr(service, "action")
    assert service.manifest()["actions"] == agent.manifest()
