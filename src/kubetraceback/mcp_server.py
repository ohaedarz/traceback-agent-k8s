import os

from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from kubetraceback.tools import mcp



@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def run() -> None:
    transport = os.environ.get("TRACEBACK_MCP_TRANSPORT", "stdio").lower()

    if transport == "http":

        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "jotorial.com",
                "jotorial.com:*",
                "www.jotorial.com",
                "www.jotorial.com:*",
                "127.0.0.1",
                "127.0.0.1:*",
                "localhost",
                "localhost:*",
            ],
            allowed_origins=[
                "https://jotorial.com",
                "https://www.jotorial.com",
                "http://localhost:6274",
                "http://127.0.0.1:6274",
            ],
        )


        mcp.settings.host = os.environ.get("TRACEBACK_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("TRACEBACK_MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    run()