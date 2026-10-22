"""Tool registration: creates the shared MCP server instance and imports
all tool modules so their @mcp.tool() decorators register at import time."""

from kubetraceback.fastmcp_compat import get_mcp_class

mcp = get_mcp_class()(
    "kube-traceback",
    instructions="Read-only diagnostics for a remote Kubernetes cluster: "
    "fuzzy pod search and crash traceback retrieval.",
)

# Import tool modules to register their tools on `mcp`.
from kubetraceback.tools import diagnose, health, pods  # noqa: E402,F401
