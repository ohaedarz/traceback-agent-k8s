"""Offline tests for startup configuration (CODE_STYLES.md rules 1-3):
all env vars are read once in config.init, with no hidden defaults."""

import pytest

from kubetraceback import config


AUTH_ENV = {
    "KEYCLOAK_ISSUER_URL": "https://keycloak.example/realms/lab",
    "KEYCLOAK_CLIENT_ID": "mcp-kubetraceback",
    "KEYCLOAK_REQUIRED_SCOPE": "k8s:readonly",
    "MCP_RESOURCE_URL": "https://mcp.example/mcp",
}

HTTP_ENV = {
    "TRACEBACK_MCP_TRANSPORT": "http",
    "TRACEBACK_MCP_HOST": "0.0.0.0",
    "TRACEBACK_MCP_PORT": "8765",
    "TRACEBACK_MCP_ALLOWED_HOSTS": "example.com, example.com:*, 127.0.0.1",
    "TRACEBACK_MCP_ALLOWED_ORIGINS": "https://example.com, http://localhost:6274",
}


def _clean_env(monkeypatch):
    for name in (
        *AUTH_ENV,
        *HTTP_ENV,
        "KEYCLOAK_NS_ROLE_PREFIX",
        "TRACEBACK_DEFAULT_NAMESPACE",
        "TRACEBACK_LOG_TAIL_LINES",
        "KUBECONFIG",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def _init(monkeypatch, env: dict) -> config._Config:
    _clean_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    config.init()
    return config.CONFIG


def test_stdio_defaults(monkeypatch):
    cfg = _init(monkeypatch, {})
    assert cfg.transport == "stdio"
    assert cfg.http_host is None
    assert cfg.http_port is None
    assert cfg.allowed_hosts == []
    assert cfg.allowed_origins == []
    assert cfg.keycloak_issuer_url is None
    assert cfg.kubeconfig is None
    assert cfg.in_cluster is False


def test_only_basic_defaults_exist(monkeypatch):
    cfg = _init(monkeypatch, {})
    # The two AGENTS.md-mandated fallbacks are the only provided defaults.
    assert cfg.default_namespace == "default"
    assert cfg.log_tail_lines == 40


def test_namespace_and_tail_lines_overridable(monkeypatch):
    cfg = _init(
        monkeypatch,
        {"TRACEBACK_DEFAULT_NAMESPACE": "demo", "TRACEBACK_LOG_TAIL_LINES": "77"},
    )
    assert cfg.default_namespace == "demo"
    assert cfg.log_tail_lines == 77


def test_invalid_tail_lines_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TRACEBACK_LOG_TAIL_LINES", "not-a-number")
    with pytest.raises(config.ConfigError, match="TRACEBACK_LOG_TAIL_LINES"):
        config.init()


def test_invalid_transport_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TRACEBACK_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_TRANSPORT"):
        config.init()


def test_http_loads_allow_lists(monkeypatch):
    cfg = _init(monkeypatch, dict(HTTP_ENV))
    assert cfg.transport == "http"
    assert cfg.http_host == "0.0.0.0"
    assert cfg.http_port == 8765
    assert cfg.allowed_hosts == ["example.com", "example.com:*", "127.0.0.1"]
    assert cfg.allowed_origins == ["https://example.com", "http://localhost:6274"]


def test_http_requires_host_port(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TRACEBACK_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TRACEBACK_MCP_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("TRACEBACK_MCP_ALLOWED_ORIGINS", "https://example.com")
    # The port is read first, so its missing-var error is reported.
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_PORT"):
        config.init()

    # With the port set, the missing host is reported.
    monkeypatch.setenv("TRACEBACK_MCP_PORT", "8765")
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_HOST"):
        config.init()


def test_http_requires_allow_lists(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TRACEBACK_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TRACEBACK_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("TRACEBACK_MCP_PORT", "8765")
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_ALLOWED_HOSTS"):
        config.init()


def test_http_invalid_port_raises(monkeypatch):
    env = dict(HTTP_ENV, TRACEBACK_MCP_PORT="not-a-port")
    _clean_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_PORT"):
        config.init()


def test_auth_requires_client_id(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", AUTH_ENV["KEYCLOAK_ISSUER_URL"])
    with pytest.raises(config.ConfigError, match="KEYCLOAK_CLIENT_ID"):
        config.init()


def test_auth_requires_resource_url(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("KEYCLOAK_ISSUER_URL", AUTH_ENV["KEYCLOAK_ISSUER_URL"])
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", AUTH_ENV["KEYCLOAK_CLIENT_ID"])
    monkeypatch.setenv("KEYCLOAK_REQUIRED_SCOPE", AUTH_ENV["KEYCLOAK_REQUIRED_SCOPE"])
    with pytest.raises(config.ConfigError, match="MCP_RESOURCE_URL"):
        config.init()


def test_auth_full(monkeypatch):
    cfg = _init(monkeypatch, dict(AUTH_ENV))
    assert cfg.keycloak_issuer_url == AUTH_ENV["KEYCLOAK_ISSUER_URL"]
    assert cfg.keycloak_client_id == "mcp-kubetraceback"
    assert cfg.keycloak_required_scope == "k8s:readonly"
    assert cfg.keycloak_ns_role_prefix == "ns:"
    assert cfg.mcp_resource_url == AUTH_ENV["MCP_RESOURCE_URL"]


def test_ns_role_prefix_overridable(monkeypatch):
    cfg = _init(monkeypatch, dict(AUTH_ENV, KEYCLOAK_NS_ROLE_PREFIX="kube:"))
    assert cfg.keycloak_ns_role_prefix == "kube:"


def test_empty_values_treated_as_unset(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TRACEBACK_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TRACEBACK_MCP_HOST", "")
    monkeypatch.setenv("TRACEBACK_MCP_PORT", "8765")
    monkeypatch.setenv("TRACEBACK_MCP_ALLOWED_HOSTS", "x")
    monkeypatch.setenv("TRACEBACK_MCP_ALLOWED_ORIGINS", "y")
    with pytest.raises(config.ConfigError, match="TRACEBACK_MCP_HOST"):
        config.init()


def test_kubeconfig_and_in_cluster(monkeypatch):
    cfg = _init(monkeypatch, {"KUBECONFIG": "/home/agent/.kube/config"})
    assert cfg.kubeconfig == "/home/agent/.kube/config"
    assert cfg.in_cluster is False

    cfg = _init(
        monkeypatch,
        {"KUBERNETES_SERVICE_HOST": "10.43.0.1", "KUBERNETES_SERVICE_PORT": "443"},
    )
    assert cfg.kubeconfig is None
    assert cfg.in_cluster is True


def test_init_is_idempotent(monkeypatch):
    _init(monkeypatch, dict(AUTH_ENV))
    cfg = _init(monkeypatch, dict(AUTH_ENV))
    assert cfg.keycloak_issuer_url == AUTH_ENV["KEYCLOAK_ISSUER_URL"]