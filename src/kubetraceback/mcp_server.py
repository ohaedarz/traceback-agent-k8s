"""MCP server entrypoint and transport setup.

Environment variables are read once at startup by
kubetraceback.config.init(); the host, port and DNS-rebinding
allow-lists for the HTTP transport come from configuration, never from
hardcoded values (CODE_STYLES.md rules 1-2).
"""

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from kubetraceback import config
from kubetraceback.tools import mcp


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def run() -> None:
    if config.CONFIG is None:
        raise RuntimeError(
            "kubetraceback.config.init() must run before mcp_server.run()"
        )

    if config.CONFIG.transport == "http":
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=config.CONFIG.allowed_hosts,
            allowed_origins=config.CONFIG.allowed_origins,
        )
        mcp.settings.host = config.CONFIG.http_host
        mcp.settings.port = config.CONFIG.http_port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    run()
