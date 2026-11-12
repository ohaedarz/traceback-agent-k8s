"""Optional Keycloak OAuth2 (OIDC) authentication for the HTTP transport.

Activated when KEYCLOAK_ISSUER_URL is configured at startup (the stdio
transport stays unauthenticated - it is a local transport).

- KeycloakTokenVerifier validates bearer JWTs (RS256) against the realm
  JWKS endpoint (cached; refetched once on an unknown kid for key
  rotation) and checks iss, aud (the MCP client id) and exp.
- Namespace authorization is role based: Keycloak client roles on the MCP
  client named ns:<namespace> grant read access to that namespace, and
  ns:* grants every namespace. Realm roles with the same naming are
  honored as well. A user without a matching role has no access.

All environment variables are read once, at startup, by
kubetraceback.config (CODE_STYLES.md rule 2). This module only consults
that configuration.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Callable

from mcp.server.auth.provider import AccessToken

from kubetraceback import config

logger = logging.getLogger(__name__)

DEFAULT_NS_ROLE_PREFIX = "ns:"
ALL_NAMESPACES = "*"
_CLOCK_SKEW_SECONDS = 30


class NamespaceNotAllowed(Exception):
    """The authenticated user has no read access to a namespace."""


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _jwks_public_key(jwk: dict[str, Any]):
    from cryptography.hazmat.primitives.asymmetric import rsa

    modulus = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    exponent = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


class KeycloakTokenVerifier:
    """Validates bearer JWTs issued by a Keycloak realm (RS256 + JWKS).

    fetch_jwks is injectable for tests; by default it GETs the realm
    protocol/openid-connect/certs endpoint.
    """

    def __init__(
        self,
        issuer_url: str,
        client_id: str,
        fetch_jwks: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.client_id = client_id
        self._fetch_jwks = fetch_jwks or self._default_fetch_jwks
        self._jwks_doc: dict[str, Any] | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return self._verify(token)
        except Exception:
            logger.debug("bearer token verification failed", exc_info=True)
            return None

    def _verify(self, token: str) -> AccessToken | None:
        header_seg, payload_seg, sig_seg = token.split(".")
        header = json.loads(_b64url_decode(header_seg))
        claims = json.loads(_b64url_decode(payload_seg))
        signature = _b64url_decode(sig_seg)
        if header.get("alg") != "RS256":
            return None
        jwk = self._jwk_for(header.get("kid"))
        if jwk is None:
            return None
        self._check_signature(f"{header_seg}.{payload_seg}", signature, jwk)
        if claims.get("iss") != self.issuer_url:
            return None
        if self._is_expired(claims.get("exp")):
            return None
        if self.client_id not in self._audiences(claims):
            return None
        return AccessToken(
            token=token,
            client_id=self.client_id,
            scopes=self._scopes(claims),
            expires_at=claims.get("exp"),
            subject=claims.get("sub"),
            claims=claims,
        )

    def _jwk_for(self, kid: str | None) -> dict[str, Any] | None:
        if not kid:
            return None
        jwk = self._find_jwk(kid)
        if jwk is None:
            # Unknown kid: refetch once to pick up a rotated signing key.
            self._jwks_doc = None
            jwk = self._find_jwk(kid)
        return jwk

    def _find_jwk(self, kid: str) -> dict[str, Any] | None:
        if self._jwks_doc is None:
            self._jwks_doc = self._fetch_jwks()
        return next(
            (k for k in self._jwks_doc.get("keys", []) if k.get("kid") == kid),
            None,
        )

    def _check_signature(self, signing_input: str, signature: bytes, jwk: dict) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        _jwks_public_key(jwk).verify(
            signature, signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()
        )

    @staticmethod
    def _is_expired(exp: Any) -> bool:
        if not isinstance(exp, (int, float)):
            return True
        return exp + _CLOCK_SKEW_SECONDS < time.time()

    @staticmethod
    def _audiences(claims: dict[str, Any]) -> list[str]:
        aud = claims.get("aud")
        if isinstance(aud, str):
            return [aud]
        if isinstance(aud, list):
            return [a for a in aud if isinstance(a, str)]
        return []

    @staticmethod
    def _scopes(claims: dict[str, Any]) -> list[str]:
        scope = claims.get("scope")
        if isinstance(scope, str):
            return scope.split()
        if isinstance(scope, list):
            return [s for s in scope if isinstance(s, str)]
        return []

    def _default_fetch_jwks(self) -> dict[str, Any]:
        import requests

        url = f"{self.issuer_url}/protocol/openid-connect/certs"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()


def build_auth_kwargs() -> dict[str, Any]:
    """FastMCP constructor kwargs enabling Keycloak auth, or {} when disabled.

    Requires kubetraceback.config.init() to have been called first.
    """
    if config.CONFIG is None or not config.CONFIG.keycloak_issuer_url:
        return {}
    try:
        from mcp.server.auth.settings import AuthSettings
    except ImportError:
        logger.warning("installed mcp SDK is too old for Keycloak auth; auth disabled")
        return {}
    settings = AuthSettings(
        issuer_url=config.CONFIG.keycloak_issuer_url,
        resource_server_url=config.CONFIG.mcp_resource_url,
        required_scopes=[config.CONFIG.keycloak_required_scope],
        # The verifier already checks the token audience itself.
        validate_token_resource=False,
    )
    return {
        "auth": settings,
        "token_verifier": KeycloakTokenVerifier(
            config.CONFIG.keycloak_issuer_url, config.CONFIG.keycloak_client_id
        ),
    }


def get_current_access_token() -> AccessToken | None:
    """The verified access token for the in-flight HTTP request, if any."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:
        return None
    return get_access_token()


def allowed_namespaces(
    claims: dict[str, Any], client_id: str
) -> frozenset[str] | None:
    """Map token roles to granted namespaces.

    Returns None when all namespaces are granted (ns:*), otherwise the
    set of granted namespace names (possibly empty).
    """
    prefix = config.CONFIG.keycloak_ns_role_prefix if config.CONFIG else DEFAULT_NS_ROLE_PREFIX
    roles: list[str] = []
    resource_access = claims.get("resource_access") or {}
    roles.extend((resource_access.get(client_id) or {}).get("roles") or [])
    roles.extend((claims.get("realm_access") or {}).get("roles") or [])
    granted: set[str] = set()
    for role in roles:
        if not role.startswith(prefix):
            continue
        namespace = role[len(prefix):]
        if namespace == ALL_NAMESPACES:
            return None
        if namespace:
            granted.add(namespace)
    return frozenset(granted)


def check_namespace(namespace: str) -> None:
    """Raise NamespaceNotAllowed when the current user may not read the ns.

    A no-op when auth is disabled (stdio transport / no KEYCLOAK_ISSUER_URL).
    """
    if config.CONFIG is None or not config.CONFIG.keycloak_issuer_url:
        return
    token = get_current_access_token()
    if token is None or not token.claims:
        raise NamespaceNotAllowed("authentication required: no valid access token in context")
    allowed = allowed_namespaces(token.claims, config.CONFIG.keycloak_client_id)
    if allowed is None or namespace in allowed:
        return
    username = token.claims.get("preferred_username") or token.subject or "unknown"
    listing = ", ".join(sorted(allowed)) if allowed else "none"
    raise NamespaceNotAllowed(
        f"namespace '{namespace}' is not accessible to user '{username}' "
        f"(allowed namespaces: {listing})"
    )