"""Lazy Kubernetes client loader.

- Outside a cluster: reads the kubeconfig pointed to by KUBECONFIG
  (falls back to the standard ~/.kube/config location when unset).
- Inside a cluster (KUBERNETES_SERVICE_HOST present, no KUBECONFIG):
  uses in-cluster service account config.

Both facts are captured once at startup in kubetraceback.config
(CODE_STYLES.md rule 2); this module performs no environment reads.

The API client is loaded lazily on first use so the server can start even
when the cluster is unreachable.
"""

from kubernetes import client, config as k8s_config

from kubetraceback import config

_core_api: client.CoreV1Api | None = None


def get_core_api() -> client.CoreV1Api:
    """Return a cached CoreV1Api, loading configuration on first call."""
    global _core_api
    if _core_api is None:
        if config.CONFIG.kubeconfig is None and config.CONFIG.in_cluster:
            k8s_config.load_incluster_config()
        else:
            # When KUBECONFIG is unset, load_kube_config uses ~/.kube/config.
            k8s_config.load_kube_config()
        _core_api = client.CoreV1Api()
    return _core_api