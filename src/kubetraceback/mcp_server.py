"""FastMCP server entrypoint for kube-traceback.

Transports:
  stdio (default)  -> python -m kubetraceback
  HTTP             -> TRACEBACK_MCP_TRANSPORT=http TRACEBACK_MCP_HOST=0.0.0.0
                      TRACEBACK_MCP_PORT=8765 python -m kubetraceback

Inspect:   fastmcp dev src/kubetraceback/mcp_server.py
"""

import os

from kubetraceback.tools import mcp

# Expose `mcp` at module level so `fastmcp dev src/kubetraceback/mcp_server.py`
# finds the server instance.


def run() -> None:
    transport = os.environ.get("TRACEBACK_MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        # FastMCP (mcp 1.x) reads these; the mcp 2.x MCPServer exposes
        # settings that accept the same values.
        mcp.settings.host = os.environ.get("TRACEBACK_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("TRACEBACK_MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    run()
