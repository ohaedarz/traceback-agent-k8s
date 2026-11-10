"""Tool registration: creates the shared MCP server instance and imports
all tool modules so their @mcp.tool() decorators register at import time."""

from kubetraceback import auth
from kubetraceback.fastmcp_compat import get_mcp_class

_INSTRUCTIONS = (
    "Read-only diagnostics for a remote Kubernetes cluster: "
    "fuzzy pod search and crash traceback retrieval."
)


def _create_server():
    server_class = get_mcp_class()
    try:
        # auth kwargs enable Keycloak auth when KEYCLOAK_ISSUER_URL is set.
        return server_class("kube-traceback", instructions=_INSTRUCTIONS, **auth.build_auth_kwargs())
    except TypeError:
        # mcp 2.x MCPServer may not accept auth kwargs.
        return server_class("kube-traceback", instructions=_INSTRUCTIONS)


mcp = _create_server()

# Import tool modules to register their tools on `mcp`.
from kubetraceback.tools import diagnose, health, pods  # noqa: E402,F401