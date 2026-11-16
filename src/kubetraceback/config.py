"""Central configuration: all environment variables are read once, here,
at application initialization (CODE_STYLES.md rule 2).

No other module in the package may read the environment. The entrypoint
calls init() before the server starts processing requests:

    from kubetraceback import config
    config.init()

When auth is enabled (KEYCLOAK_ISSUER_URL is set) and a required auth
variable is missing, init() raises ConfigError so the server fails fast
at startup instead of at the first request.

Only the two AGENTS.md-mandated fallbacks are provided (the "very basic
and usual values" allowed by CODE_STYLES.md rule 3):
TRACEBACK_DEFAULT_NAMESPACE -> default and
TRACEBACK_LOG_TAIL_LINES -> 40.
"""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """A required environment variable is missing or invalid."""


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(f"environment variable {name} is required but not set")
    return value


def _int_env(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as err:
        raise ConfigError(
            f"TRACEBACK_LOG_TAIL_LINES must be an integer: {err}"
        ) from None


def _list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def init() -> None:
    """Read every environment variable exactly once and validate it.

    The entrypoint calls this during startup, before any request is
    processed; repeated calls simply re-read the environment.
    """
    global CONFIG
    CONFIG = _Config(_load())


def _load() -> dict:
    env = os.environ

    transport = (env.get("TRACEBACK_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport not in ("stdio", "http"):
        raise ConfigError(
            f"TRACEBACK_MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}"
        )

    settings: dict = {
        "transport": transport,
        "default_namespace": env.get("TRACEBACK_DEFAULT_NAMESPACE") or "default",
        "log_tail_lines": _int_env(env.get("TRACEBACK_LOG_TAIL_LINES") or "40"),
        "kubeconfig": env.get("KUBECONFIG") or None,
        "in_cluster": env.get("KUBERNETES_SERVICE_HOST") or None,
        "keycloak_issuer_url": env.get("KEYCLOAK_ISSUER_URL") or None,
    }

    if transport == "http":
        try:
            port_raw = _required("TRACEBACK_MCP_PORT")
            settings.update(
                http_host=_required("TRACEBACK_MCP_HOST"),
                http_port=int(port_raw),
                allowed_hosts=_list(_required("TRACEBACK_MCP_ALLOWED_HOSTS")),
                allowed_origins=_list(_required("TRACEBACK_MCP_ALLOWED_ORIGINS")),
            )
        except ValueError as err:
            raise ConfigError(f"TRACEBACK_MCP_PORT must be an integer: {err}") from None

    if settings["keycloak_issuer_url"]:
        settings.update(
            keycloak_client_id=_required("KEYCLOAK_CLIENT_ID"),
            keycloak_required_scope=_required("KEYCLOAK_REQUIRED_SCOPE"),
            keycloak_ns_role_prefix=env.get("KEYCLOAK_NS_ROLE_PREFIX") or "ns:",
            mcp_resource_url=_required("MCP_RESOURCE_URL"),
        )

    return settings


class _Config:
    """The frozen configuration snapshot loaded by init()."""

    def __init__(self, settings: dict) -> None:
        self._s = settings

    @property
    def transport(self) -> str:
        return self._s["transport"]

    @property
    def http_host(self) -> str | None:
        return self._s.get("http_host")

    @property
    def http_port(self) -> int | None:
        return self._s.get("http_port")

    @property
    def allowed_hosts(self) -> list[str]:
        return list(self._s.get("allowed_hosts", []))

    @property
    def allowed_origins(self) -> list[str]:
        return list(self._s.get("allowed_origins", []))

    @property
    def default_namespace(self) -> str:
        return self._s["default_namespace"]

    @property
    def log_tail_lines(self) -> int:
        return self._s["log_tail_lines"]

    @property
    def kubeconfig(self) -> str | None:
        return self._s["kubeconfig"]

    @property
    def in_cluster(self) -> bool:
        return bool(self._s["in_cluster"])

    @property
    def keycloak_issuer_url(self) -> str | None:
        return self._s["keycloak_issuer_url"]

    @property
    def keycloak_client_id(self) -> str | None:
        return self._s.get("keycloak_client_id")

    @property
    def keycloak_required_scope(self) -> str | None:
        return self._s.get("keycloak_required_scope")

    @property
    def keycloak_ns_role_prefix(self) -> str:
        return self._s.get("keycloak_ns_role_prefix", "ns:")

    @property
    def mcp_resource_url(self) -> str | None:
        return self._s.get("mcp_resource_url")


CONFIG: _Config | None = None
