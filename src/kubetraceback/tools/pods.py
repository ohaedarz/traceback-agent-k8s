"""Fuzzy pod name search."""

from kubetraceback import auth, k8s_client
from kubetraceback.tools import mcp

_MAX_RESULTS = 10


@mcp.tool()
def find_pods_by_name(search_keyword: str, namespace: str = "default") -> str:
    """
    Resolve a fuzzy keyword to exact pod names in a namespace.
    Returns a compact table of matching pod names and their phase.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        pods = k8s_client.get_core_api().list_namespaced_pod(namespace=ns).items
        # Normalize: lowercase and replace spaces with hyphens so that
        # "web app" matches pod names like "web-app-7f9d4b8c6-abcde".
        keyword = search_keyword.lower().replace(" ", "-")
        matches = [
            p
            for p in pods
            if keyword in p.metadata.name.lower()
            or keyword in "-".join((p.metadata.labels or {}).values()).lower()
        ][:_MAX_RESULTS]
        if not matches:
            return f"No pods in namespace '{ns}' match '{search_keyword}'."
        lines = ["| Pod | Phase |", "|---|---|"]
        lines += [f"| {p.metadata.name} | {p.status.phase} |" for p in matches]
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list pods in namespace '{namespace}': {e}"
