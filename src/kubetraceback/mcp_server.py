import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from kubetraceback.tools import mcp


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def run() -> None:
    transport = os.environ.get("TRACEBACK_MCP_TRANSPORT", "stdio").lower()

    if transport == "http":
        mcp.settings.host = os.environ.get("TRACEBACK_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("TRACEBACK_MCP_PORT", "8765"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    run()