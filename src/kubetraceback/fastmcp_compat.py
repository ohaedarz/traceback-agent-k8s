"""Compatibility shim: FastMCP (mcp 1.x) or MCPServer (mcp 2.x).

This project targets the mcp 1.x FastMCP API. If a newer mcp 2.x is installed
(where FastMCP was renamed to MCPServer), fall back gracefully so the server
still starts and tools can be registered.
"""

try:
    from mcp.server.fastmcp import FastMCP as _MCPClass  # type: ignore
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as _MCPClass  # type: ignore


def get_mcp_class():
    """Return the MCP server class available in the installed SDK version."""
    return _MCPClass
