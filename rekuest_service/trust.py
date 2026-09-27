"""Service-to-service trust: each instance holds its own key, the coord vouches for the public halves.

Every service instance of a hub has one Ed25519 key (``settings.INSTANCE["PRIVATE_KEY"]``) — the
only secret it holds for talking to its hub's other services. The hub's coord (lok) publishes
the public halves of all of them as a JWKS, the **trust bundle** (``/.well-known/hub-keys/<hub>``;
each key carries the ``service`` it belongs to). Nothing is shared between two services.

A request from one service to another carries a short-lived JWT::

    Authorization: RekuestService <jwt>
    header  {alg: Ed25519, kid: <RFC 7638 thumbprint>, typ: rekuest-service+jwt}
    claims  {iss: <sender's identifier>, aud: <receiver's identifier>, iat, exp (60 s), jti,
             htm: <method>, htu: <path>, bh: <base64url sha256 of the body>}

A receiver finds ``kid`` in the bundle, requires that key's ``service`` to be the claimed
``iss`` (so one service cannot speak as another), and checks ``aud``, the time window, and that
method, path and body are the ones signed. Replay protection beyond the window is the receiver's
business (rekuest claims ``jti`` in redis).

Settings (``settings.INSTANCE``, from the service's ``instance`` config block)::

    PRIVATE_KEY     this instance's Ed25519 private key, PKCS#8 PEM
    TRUST_JWKS_URI  the coord's hub-keys URL (cached; refetched on an unknown kid)
    TRUST_JWKS      or: the bundle inline, for a hub that is not enrolled yet
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from joserfc import jwt
from joserfc.jwk import KeySet, OKPKey

logger = logging.getLogger(__name__)

SCHEME = "RekuestService"
TYP = "rekuest-service+jwt"
ALGORITHM = "Ed25519"
LIFETIME_SECONDS = 60
BUNDLE_TTL_SECONDS = 300
REFETCH_MIN_SECONDS = 60


class TrustError(Exception):
    """A service request that does not prove who sent it. The message says why (safe to log)."""


def _instance_settings() -> dict[str, Any]:
    return getattr(settings, "INSTANCE", None) or {}


def body_hash(body: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode("ascii")


def public_jwk(key: OKPKey) -> dict[str, Any]:
    """The public JWK of ``key`` with its thumbprint as ``kid`` — what a trust bundle lists."""
    return {**key.as_dict(private=False), "kid": key.thumbprint(), "use": "sig", "alg": ALGORITHM}


def raw_public_key_b64(key: OKPKey) -> str:
    """The raw 32-byte public key, base64 — the fakts manifest's ``challenge_key``."""
    x = key.as_dict(private=False)["x"]
    return base64.b64encode(base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))).decode("ascii")


# --- this instance's key ---------------------------------------------------------------------

_key_lock = threading.Lock()
_key_cache: dict[str, OKPKey] = {}


def instance_key() -> OKPKey | None:
    """This instance's signing key, or None when none is configured (then nothing can be signed)."""
    pem = _instance_settings().get("PRIVATE_KEY")
    if not pem:
        return None
    with _key_lock:
        key = _key_cache.get(pem)
        if key is None:
            key = OKPKey.import_key(pem)
            _key_cache.clear()
            _key_cache[pem] = key
        return key


# --- the trust bundle ------------------------------------------------------------------------


class TrustBundle:
    """The hub's instance public keys: inline, or fetched from the coord and cached."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, dict[str, Any]] = {}
        self._source: tuple | None = None
        self._fetched_at = 0.0
        self._last_attempt = 0.0

    def _configured(self) -> tuple[str | None, dict | None]:
        conf = _instance_settings()
        return conf.get("TRUST_JWKS_URI"), conf.get("TRUST_JWKS")

    def _load(self, jwks: dict[str, Any]) -> None:
        keys = {}
        for jwk in jwks.get("keys") or []:
            if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519" and jwk.get("kid"):
                keys[jwk["kid"]] = jwk
        self._keys = keys

    def _fetch(self, uri: str) -> None:
        self._last_attempt = time.monotonic()
        response = httpx.get(uri, timeout=10.0)
        response.raise_for_status()
        self._load(response.json())
        self._fetched_at = time.monotonic()

    def get(self, kid: str) -> dict[str, Any] | None:
        """The JWK for ``kid`` (with its ``service``), or None when the bundle does not vouch for it."""
        uri, inline = self._configured()
        with self._lock:
            source = (uri, id(inline) if inline is not None else None)
            if source != self._source:
                self._source, self._keys, self._fetched_at, self._last_attempt = source, {}, 0.0, 0.0
                if inline is not None:
                    self._load(inline)
            if uri:
                now = time.monotonic()
                stale = now - self._fetched_at > BUNDLE_TTL_SECONDS
                missing = kid not in self._keys and now - self._last_attempt > REFETCH_MIN_SECONDS
                if stale or missing:
                    try:
                        self._fetch(uri)
                    except Exception as error:  # noqa: BLE001  keep the last bundle; a coord outage must not drop trust
                        logger.warning("Could not fetch the hub trust bundle from %s: %s", uri, error)
            return self._keys.get(kid)


trust_bundle = TrustBundle()


# --- signing and verifying -------------------------------------------------------------------


def sign(method: str, path: str, body: bytes, *, issuer: str, audience: str, key: OKPKey | None = None) -> str:
    """The ``Authorization`` header value for a request from ``issuer`` to ``audience``.

    Signed with this instance's key, or with ``key`` when given.
    """
    key = key or instance_key()
    if key is None:
        raise TrustError("No instance key configured (settings.INSTANCE['PRIVATE_KEY'])")
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + LIFETIME_SECONDS,
        "jti": uuid.uuid4().hex,
        "htm": method.upper(),
        "htu": path,
        "bh": body_hash(body),
    }
    header = {"alg": ALGORITHM, "kid": key.thumbprint(), "typ": TYP}
    return f"{SCHEME} {jwt.encode(header, claims, key, algorithms=[ALGORITHM])}"


@dataclass(frozen=True)
class Verified:
    """Who sent a request that verified: the sender's service identifier, and the token's id."""

    issuer: str
    jti: str
    expires_at: int


def verify(method: str, path: str, body: bytes, authorization: str | None, *, audience: str, max_skew: int = 30) -> Verified:
    """Check a request's service JWT; the sender, or ``TrustError``."""
    if not authorization or not authorization.startswith(f"{SCHEME} "):
        raise TrustError("No service token")
    token = authorization[len(SCHEME) + 1 :].strip()
    kid = _unverified_kid(token)
    if not kid:
        raise TrustError("Service token without a key id")
    jwk = trust_bundle.get(kid)
    if jwk is None:
        raise TrustError(f"No key {kid} in the hub's trust bundle")
    try:
        decoded = jwt.decode(token, KeySet.import_key_set({"keys": [jwk]}), algorithms=[ALGORITHM])
    except Exception as error:  # noqa: BLE001
        raise TrustError(f"Bad service token signature: {error}") from None
    if decoded.header.get("typ") != TYP:
        raise TrustError("Not a service token")
    claims = decoded.claims
    now = int(time.time())
    issuer = claims.get("iss")
    if not issuer or jwk.get("service") != issuer:
        raise TrustError(f"Key {kid} belongs to {jwk.get('service')!r}, not to {issuer!r}")
    if claims.get("aud") != audience:
        raise TrustError(f"Token is for {claims.get('aud')!r}, not {audience!r}")
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int) or exp < now - max_skew or iat > now + max_skew or exp - iat > LIFETIME_SECONDS + max_skew:
        raise TrustError("Service token expired or not yet valid")
    if claims.get("htm") != method.upper() or claims.get("htu") != path:
        raise TrustError("Service token was signed for another request")
    if claims.get("bh") != body_hash(body):
        raise TrustError("Service token was signed for another body")
    if not claims.get("jti"):
        raise TrustError("Service token without an id")
    return Verified(issuer=issuer, jti=str(claims["jti"]), expires_at=exp)


def _unverified_kid(token: str) -> str | None:
    """The ``kid`` of a compact JWS header, read without verifying (verification follows)."""
    import json

    try:
        segment = token.split(".", 1)[0]
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))).get("kid")
    except Exception:  # noqa: BLE001
        return None
