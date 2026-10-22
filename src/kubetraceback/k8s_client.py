"""Lazy Kubernetes client loader.

- Outside a cluster: reads KUBECONFIG (default ~/.kube/config).
- Inside a cluster (no KUBECONFIG set): uses in-cluster service account config.

The client is loaded lazily on first use so the server can start even when the
cluster is unreachable.
"""

import os

from kubernetes import client, config

_core_api: client.CoreV1Api | None = None


def get_core_api() -> client.CoreV1Api:
    """Return a cached CoreV1Api, loading configuration on first call."""
    global _core_api
    if _core_api is None:
        if os.environ.get("KUBECONFIG") or not _in_cluster():
            config.load_kube_config()
        else:
            config.load_incluster_config()
        _core_api = client.CoreV1Api()
    return _core_api


def _in_cluster() -> bool:
    """True when the standard in-cluster service-account token is present."""
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def default_namespace() -> str:
    return os.environ.get("TRACEBACK_DEFAULT_NAMESPACE", "default")


def log_tail_lines() -> int:
    try:
        return int(os.environ.get("TRACEBACK_LOG_TAIL_LINES", "40"))
    except ValueError:
        return 40
