"""MCP health / connectivity check tools."""

import platform
import time
import uuid

from kubetraceback import auth, k8s_client
from kubetraceback.tools import mcp

_START_TIME = time.monotonic()
_INSTANCE_ID = uuid.uuid4().hex[:8]


def _uptime_seconds() -> str:
    return f"{time.monotonic() - _START_TIME:.0f}s"


@mcp.tool()
def handshake() -> str:
    """
    MCP health check. Confirms the server process is alive and reports its
    uptime without contacting the cluster. Use as a connectivity smoke test.
    """
    try:
        return (
            "pong: kube-traceback MCP server is alive "
            f"(instance {_INSTANCE_ID}, python {platform.python_version()}, "
            f"uptime {_uptime_seconds()}). No cluster contact made."
        )
    except Exception as e:
        return f"handshake failed: {e}"


@mcp.tool()
def cluster_status(namespace: str | None = None) -> str:
    """
    Deep health check. Lists pods in a namespace (read-only) to verify the
    kubeconfig/in-cluster connection works, returning pod count or error.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        pods = k8s_client.get_core_api().list_namespaced_pod(namespace=ns).items
        return f"cluster reachable: {len(pods)} pod(s) in namespace '{ns}'."
    except Exception as e:
        return f"cluster unreachable: {e}"
