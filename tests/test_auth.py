"""Offline tests for Keycloak auth: JWT verification, namespace RBAC, env wiring.

No network access: the JWKS fetcher is injected, and signing keys are
generated locally. Requires `cryptography` (transitive dep of mcp[cli]).
"""

import base64
import json
import time
from unittest import mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kubetraceback import auth

ISSUER = "https://keycloak.example.com/realms/lab"
CLIENT = "mcp-kubetraceback"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


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
    """Build a signed RS256 JWT. ``sign_input`` overrides what gets signed
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
# auth_enabled / _env
# ---------------------------------------------------------------------------

def test_auth_enabled_false_without_issuer(monkeypatch):
    monkeypatch.delenv("KEYCLOAK_ISSUER_URL", raising=False)
    assert auth.auth_enabled() is False


def test_auth_enabled_true_with_issuer(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    assert auth.auth_enabled() is True


def test_env_falls_back_on_empty(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "")
    assert auth._env("SOME_TEST_VAR", "default") == "default"
    monkeypatch.setenv("SOME_TEST_VAR", "value")
    assert auth._env("SOME_TEST_VAR", "default") == "value"
    monkeypatch.delenv("SOME_TEST_VAR", raising=False)
    assert auth._env("SOME_TEST_VAR") is None


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


def test_allowed_namespaces_wildcard_from_client_roles():
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["ns:*"]}}
    assert auth.allowed_namespaces(claims, CLIENT) is None


def test_allowed_namespaces_wildcard_from_realm_roles():
    claims = base_claims()
    claims["realm_access"] = {"roles": ["ns:*"]}
    assert auth.allowed_namespaces(claims, CLIENT) is None


def test_allowed_namespaces_wildcard_client_beats_realm():
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["ns:demo", "ns:*"]}}
    assert auth.allowed_namespaces(claims, CLIENT) is None


def test_allowed_namespaces_union_client_and_realm():
    claims = base_claims()
    claims["realm_access"] = {"roles": ["ns:staging"]}
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset({"demo", "prod", "staging"})


def test_allowed_namespaces_empty_when_no_ns_roles():
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["k8s:readonly"]}}
    claims["realm_access"] = {"roles": ["admin"]}
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset()


def test_allowed_namespaces_missing_resource_access():
    claims = base_claims()
    claims.pop("resource_access")
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset()


def test_allowed_namespaces_custom_prefix(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_NS_ROLE_PREFIX", "kube:")
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["kube:one", "ns:two"]}}
    # Only the custom prefix is honored; the old "ns:" role is ignored.
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset({"one"})


def test_allowed_namespaces_ignores_non_matching_client():
    claims = base_claims()
    claims["resource_access"] = {
        "other-client": {"access": {}, "roles": ["ns:other"]},
        CLIENT: {"access": {}, "roles": []},
    }
    assert auth.allowed_namespaces(claims, CLIENT) == frozenset()


# ---------------------------------------------------------------------------
# check_namespace
# ---------------------------------------------------------------------------

def test_check_namespace_noop_when_auth_disabled(monkeypatch):
    monkeypatch.delenv("KEYCLOAK_ISSUER_URL", raising=False)
    # No token in context, but auth is off -> no exception.
    auth.check_namespace("any-namespace")


def test_check_namespace_requires_token_when_enabled(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    # Patch get_current_access_token to return None.
    with mock.patch.object(auth, "get_current_access_token", return_value=None):
        with pytest.raises(auth.NamespaceNotAllowed, match="authentication required"):
            auth.check_namespace("demo")


def _make_access_token(claims: dict) -> "auth.AccessToken":
    from mcp.server.auth.provider import AccessToken

    return AccessToken(
        token="t", client_id=CLIENT, scopes=[], expires_at=None,
        subject=claims.get("sub"), claims=claims,
    )


def test_check_namespace_granted(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    token = _make_access_token(base_claims())  # ns:demo, ns:prod
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        auth.check_namespace("demo")  # no raise
        auth.check_namespace("prod")  # no raise


def test_check_namespace_denied(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    token = _make_access_token(base_claims())  # ns:demo, ns:prod
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed) as excinfo:
            auth.check_namespace("kube-system")
    msg = str(excinfo.value)
    assert "kube-system" in msg
    assert "jdoe" in msg  # preferred_username surfaced
    assert "demo" in msg and "prod" in msg  # allowed list surfaced


def test_check_namespace_wildcard_allows_any(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    claims = base_claims()
    claims["realm_access"] = {"roles": ["ns:*"]}
    token = _make_access_token(claims)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        auth.check_namespace("whatever")  # no raise


def test_check_namespace_empty_roles_denies_everything(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    claims = base_claims()
    claims["resource_access"] = {CLIENT: {"access": {}, "roles": ["k8s:readonly"]}}
    claims["realm_access"] = {"roles": []}
    token = _make_access_token(claims)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed, match="none"):
            auth.check_namespace("demo")


def test_check_namespace_token_without_claims(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    from mcp.server.auth.provider import AccessToken

    token = AccessToken(token="t", client_id=CLIENT, scopes=[], expires_at=None,
                        subject=None, claims=None)
    with mock.patch.object(auth, "get_current_access_token", return_value=token):
        with pytest.raises(auth.NamespaceNotAllowed, match="authentication required"):
            auth.check_namespace("demo")


def test_check_namespace_via_real_contextvar(monkeypatch):
    """Exercise the real get_current_access_token via the SDK contextvar."""
    import asyncio

    from mcp.server.auth.middleware import auth_context

    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
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

def test_build_auth_kwargs_disabled_without_issuer(monkeypatch):
    monkeypatch.delenv("KEYCLOAK_ISSUER_URL", raising=False)
    assert auth.build_auth_kwargs() == {}


def test_build_auth_kwargs_enabled(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "custom-client")
    monkeypatch.setenv("KEYCLOAK_REQUIRED_SCOPE", "custom:scope")
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://mcp.example/mcp")
    kw = auth.build_auth_kwargs()
    assert set(kw) == {"auth", "token_verifier"}
    assert str(kw["auth"].issuer_url) == ISSUER
    assert str(kw["auth"].resource_server_url) == "https://mcp.example/mcp"
    assert kw["auth"].required_scopes == ["custom:scope"]
    assert kw["token_verifier"].issuer_url == ISSUER
    assert kw["token_verifier"].client_id == "custom-client"


def test_build_auth_kwargs_defaults(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", ISSUER)
    monkeypatch.delenv("KEYCLOAK_CLIENT_ID", raising=False)
    monkeypatch.delenv("KEYCLOAK_REQUIRED_SCOPE", raising=False)
    monkeypatch.delenv("MCP_RESOURCE_URL", raising=False)
    monkeypatch.setenv("TRACEBACK_MCP_HOST", "10.0.0.5")
    monkeypatch.setenv("TRACEBACK_MCP_PORT", "9000")
    kw = auth.build_auth_kwargs()
    assert str(kw["auth"].resource_server_url) == "http://10.0.0.5:9000/mcp"
    assert kw["auth"].required_scopes == [auth.DEFAULT_REQUIRED_SCOPE]
    assert kw["token_verifier"].client_id == auth.DEFAULT_CLIENT_ID