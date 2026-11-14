"""Offline tests for Keycloak auth: JWT verification, namespace RBAC,
config wiring.

No network access: the JWKS fetcher is injected, and signing keys are
generated locally. Requires cryptography (transitive dep of mcp[cli]).

Auth behavior is driven by the startup config snapshot (CODE_STYLES.md
rule 2): tests set fields on the app_config fixture instead of env vars.
"""

import base64
import json
import time
from unittest import mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kubetraceback import auth, config

ISSUER = "https://keycloak.example.com/realms/lab"
CLIENT = "mcp-kubetraceback"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _auth_config(**overrides) -> config._Config:
    base = dict(
        keycloak_issuer_url=ISSUER,
        keycloak_client_id=CLIENT,
        keycloak_required_scope="k8s:readonly",
        keycloak_ns_role_prefix="ns:",
        mcp_resource_url="https://mcp.example/mcp",
    )
    base.update(overrides)
    return config._Config(base)


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()

    def num_bytes(n: int) -> bytes:
        return n.to_bytes((n.bit_length() + 7) // 8, "big")

    return key, {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "k1",
        "n": _b64url(num_bytes(pub.n)).decode(),
        "e": _b64url(num_bytes(pub.e)).decode(),
    }


@pytest.fixture(scope="module")
def signing_key(rsa_keypair):
    return rsa_keypair[0]


def make_token(
    signing_key,
    jwk: dict,
    claims: dict,
    *,
    header_overrides: dict | None = None,
    sign_input: str | None = None,
) -> str:
    """Build a signed RS256 JWT. sign_input overrides what gets signed
    (to produce an invalid signature)."""
    header = {"alg": "RS256", "typ": "JWT", "kid": jwk.get("kid", "k1")}
    header.update(header_overrides or {})
    payload = _b64url(json.dumps(header).encode()) + b"." + _b64url(
        json.dumps(claims).encode()
    )
    sig_input = sign_input if sign_input is not None else payload.decode()
    sig = signing_key.sign(sig_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return (payload + b"." + _b64url(sig)).decode()


def base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "user123",
        "iat": now - 10,
        "exp": now + 3600,
        "preferred_username": "jdoe",
        "scope": "k8s:readonly profile",
        "resource_access": {CLIENT: {"access": {}, "roles": ["ns:demo", "ns:prod"]}},
        "realm_access": {"roles": []},
    }
    claims.update(overrides)
    return claims


def make_verifier(
    rsa_keypair,
    fetch_jwks=None,
    *,
    issuer: str = ISSUER,
    client_id: str = CLIENT,
) -> auth.KeycloakTokenVerifier:
    key, jwk = rsa_keypair
    if fetch_jwks is None:
        fetch_jwks = lambda: {"keys": [jwk]}
    return auth.KeycloakTokenVerifier(
        issuer_url=issuer, client_id=client_id, fetch_jwks=fetch_jwks
    )


# ---------------------------------------------------------------------------
# KeycloakTokenVerifier.verify_token
# ---------------------------------------------------------------------------

def test_verify_valid_token(rsa_keypair):
    v = make_verifier(rsa_keypair)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims())
    acc = pytest.importorskip("asyncio").run(v.verify_token(token))
    assert acc is not None
    assert acc.token == token
    assert acc.client_id == CLIENT
    assert acc.subject == "user123"
    assert acc.expires_at == base_claims()["exp"]
    assert acc.claims["preferred_username"] == "jdoe"
    assert acc.scopes == ["k8s:readonly", "profile"]


def test_verify_rejects_bad_signature(rsa_keypair):
    key, jwk = rsa_keypair
    v = make_verifier(rsa_keypair)
    # Sign the payload with the wrong key
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = base_claims()
    header = {"alg": "RS256", "typ": "JWT", "kid": jwk["kid"]}
    payload = _b64url(json.dumps(header).encode()) + b"." + _b64url(
        json.dumps(claims).encode()
    )
    sig = other.sign(payload.decode().encode(), padding.PKCS1v15(), hashes.SHA256())
    forged = (payload + b"." + _b64url(sig)).decode()
    assert pytest.importorskip("asyncio").run(v.verify_token(forged)) is None


def test_verify_rejects_wrong_issuer(rsa_keypair):
    v = make_verifier(rsa_keypair)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims(iss="https://other.example/realms/x"))
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_rejects_expired_token(rsa_keypair):
    v = make_verifier(rsa_keypair)
    now = int(time.time())
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims(exp=now - 60))
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_accepts_token_within_clock_skew(rsa_keypair):
    v = make_verifier(rsa_keypair)
    now = int(time.time())
    # Expired 10 s ago, within the 30 s skew window.
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims(exp=now - 10))
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is not None


def test_verify_rejects_missing_exp(rsa_keypair):
    v = make_verifier(rsa_keypair)
    claims = base_claims()
    claims.pop("exp")
    token = make_token(rsa_keypair[0], rsa_keypair[1], claims)
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_rejects_wrong_audience_string(rsa_keypair):
    v = make_verifier(rsa_keypair)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims(aud="other-client"))
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_accepts_audience_list_containing_client(rsa_keypair):
    v = make_verifier(rsa_keypair)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims(aud=["other", CLIENT]))
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is not None


def test_verify_rejects_non_rs256_alg(rsa_keypair):
    key, jwk = rsa_keypair
    v = make_verifier(rsa_keypair)
    token = make_token(
        key, jwk, base_claims(), header_overrides={"alg": "HS256", "kid": jwk["kid"]}
    )
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_rejects_unknown_kid(rsa_keypair):
    key, jwk = rsa_keypair
    v = make_verifier(rsa_keypair)
    # Header kid not present in the JWKS doc; refetch still misses.
    token = make_token(key, jwk, base_claims(), header_overrides={"kid": "unknown"})
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


def test_verify_refetches_jwks_on_unknown_kid(rsa_keypair):
    """A rotated kid is picked up by a single refetch."""
    key, jwk = rsa_keypair
    state = {"call_count": 0}
    rotated_jwk = dict(jwk, kid="rotated-kid")

    def fetch():
        state["call_count"] += 1
        if state["call_count"] == 1:
            return {"keys": [jwk]}  # old key only
        return {"keys": [rotated_jwk]}  # rotated key on refetch

    v = make_verifier(rsa_keypair, fetch_jwks=fetch)
    token = make_token(key, rotated_jwk, base_claims(), header_overrides={"kid": "rotated-kid"})
    acc = pytest.importorskip("asyncio").run(v.verify_token(token))
    assert acc is not None
    assert state["call_count"] == 2


def test_verify_caches_jwks_for_known_kid(rsa_keypair):
    key, jwk = rsa_keypair
    state = {"call_count": 0}

    def fetch():
        state["call_count"] += 1
        return {"keys": [jwk]}

    v = make_verifier(rsa_keypair, fetch_jwks=fetch)
    token = make_token(key, jwk, base_claims())
    asyncio = pytest.importorskip("asyncio")
    assert asyncio.run(v.verify_token(token)) is not None
    assert asyncio.run(v.verify_token(token)) is not None
    assert state["call_count"] == 1  # cached, not refetched


def test_verify_returns_none_on_malformed_token(rsa_keypair):
    v = make_verifier(rsa_keypair)
    assert pytest.importorskip("asyncio").run(v.verify_token("not-a-jwt")) is None
    assert pytest.importorskip("asyncio").run(v.verify_token("a.b")) is None


def test_verify_populates_resource_for_backend_matching(rsa_keypair):
    """The verifier must set AccessToken.resource so BearerAuthBackend's
    resource_server_url comparison does not drop the token."""
    v = make_verifier(rsa_keypair)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims())
    acc = pytest.importorskip("asyncio").run(v.verify_token(token))
    assert acc is not None
    assert acc.resource == CLIENT


def test_verify_uses_resource_claim_when_present(rsa_keypair):
    v = make_verifier(rsa_keypair)
    claims = base_claims(resource="https://mcp.example/mcp")
    token = make_token(rsa_keypair[0], rsa_keypair[1], claims)
    acc = pytest.importorskip("asyncio").run(v.verify_token(token))
    assert acc is not None
    assert acc.resource == "https://mcp.example/mcp"


def test_verify_returns_none_on_fetch_error(rsa_keypair):
    def boom():
        raise RuntimeError("network down")

    v = make_verifier(rsa_keypair, fetch_jwks=boom)
    token = make_token(rsa_keypair[0], rsa_keypair[1], base_claims())
    assert pytest.importorskip("asyncio").run(v.verify_token(token)) is None


# ---------------------------------------------------------------------------
# allowed_namespaces
# ---------------------------------------------------------------------------

def test_allowed_namespaces_from_client_roles():
    claims = base_claims()
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset({"demo", "prod"})


def test_allowed_namespaces_wildcard():
    claims = base_claims()
    claims["realm_access"] = {"roles": ["ns:*"]}
    assert auth.allowed_namespaces(claims, CLIENT) is None


def test_allowed_namespaces_custom_prefix():
    config.CONFIG = _auth_config(keycloak_ns_role_prefix="kube:")
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["kube:alpha"]}}
    claims["realm_access"] = {"roles": []}
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset({"alpha"})


def test_allowed_namespaces_empty_when_no_roles():
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["k8s:readonly"]}}
    claims["realm_access"] = {"roles": []}
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset()


# ---------------------------------------------------------------------------
# check_namespace
# ---------------------------------------------------------------------------

def _make_access_token(claims: dict) -> "auth.AccessToken":
    from mcp.server.auth.provider import AccessToken

    return AccessToken(
        token="t", client_id=CLIENT, scopes=[], expires_at=None,
        subject=claims.get("sub"), claims=claims,
    )


def test_check_namespace_noop_when_auth_disabled():
    # Default fixture config has no issuer URL.
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(token="t", client_id=CLIENT, scopes=[], expires_at=None,
                        subject=None, claims=None)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        auth.check_namespace("anything")  # no raise


def test_check_namespace_granted():
    config.CONFIG = _auth_config()
    token = _make_access_token(base_claims())  # ns:demo, ns:prod
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        auth.check_namespace("demo")  # no raise
        auth.check_namespace("prod")  # no raise


def test_check_namespace_denied():
    config.CONFIG = _auth_config()
    token = _make_access_token(base_claims())  # ns:demo, ns:prod
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed) as excinfo:
            auth.check_namespace("kube-system")
    msg = str(excinfo.value)
    assert "kube-system" in msg
    assert "jdoe" in msg  # preferred_username surfaced
    assert "demo" in msg and "prod" in msg  # allowed list surfaced


def test_check_namespace_wildcard_allows_any():
    config.CONFIG = _auth_config()
    claims = base_claims()
    claims["realm_access"] = {"roles": ["ns:*"]}
    token = _make_access_token(claims)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        auth.check_namespace("whatever")  # no raise


def test_check_namespace_empty_roles_denies_everything():
    config.CONFIG = _auth_config()
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["k8s:readonly"]}}
    claims["realm_access"] = {"roles": []}
    token = _make_access_token(claims)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed, match="none"):
            auth.check_namespace("demo")


def test_check_namespace_requires_token_when_enabled():
    config.CONFIG = _auth_config()
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(token="t", client_id=CLIENT, scopes=[], expires_at=None,
                        subject=None, claims=None)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed, match="authentication required"):
            auth.check_namespace("demo")


def test_check_namespace_via_real_contextvar():
    """Exercise the real get_current_access_token via the SDK contextvar."""
    from mcp.server.auth.middleware import auth_context

    config.CONFIG = _auth_config()
    claims = base_claims()
    token = _make_access_token(claims)
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    user = AuthenticatedUser(token)
    token_var = auth_context.auth_context_var.set(user)
    try:
        assert auth.get_current_access_token() is token
        auth.check_namespace("demo")  # granted
        with pytest.raises(auth.NamespaceNotAllowed):
            auth.check_namespace("kube-system")
    finally:
        auth_context.auth_context_var.reset(token_var)


# ---------------------------------------------------------------------------
# build_auth_kwargs
# ---------------------------------------------------------------------------

def test_build_auth_kwargs_disabled_without_issuer():
    # Default fixture config has no issuer URL.
    assert auth.build_auth_kwargs() == {}


def test_build_auth_kwargs_disabled_before_init():
    saved = config.CONFIG
    try:
        config.CONFIG = None
        assert auth.build_auth_kwargs() == {}
    finally:
        config.CONFIG = saved


def test_build_auth_kwargs_enabled():
    config.CONFIG = _auth_config(
        keycloak_client_id="custom-client",
        keycloak_required_scope="custom:scope",
        mcp_resource_url="https://mcp.example/mcp",
    )
    kw = auth.build_auth_kwargs()
    assert set(kw) == {"auth", "token_verifier"}
    assert str(kw["auth"].issuer_url) == ISSUER
    assert str(kw["auth"].resource_server_url) == "https://mcp.example/mcp"
    assert kw["auth"].required_scopes == ["custom:scope"]
    assert kw["token_verifier"].issuer_url == ISSUER
    assert kw["token_verifier"].client_id == "custom-client"