"""Offline tests for the kube-traceback tools."""

from kubetraceback.tools import diagnose, health, pods


def test_find_pods_fuzzy_match(mock_k8s_api, running_pod, crash_pod):
    mock_k8s_api.list_namespaced_pod.return_value = type(
        "R", (), {"items": [running_pod, crash_pod]}
    )()
    result = pods.find_pods_by_name("web app", namespace="default")
    assert "web-app-7f9d4b8c6-abcde" in result
    assert "web-app-7f9d4b8c6-x2k1p" in result
    assert "Running" in result and "CrashLoopBackOff" in result
    mock_k8s_api.list_namespaced_pod.assert_called_once_with(namespace="default")


def test_find_pods_no_match(mock_k8s_api, running_pod):
    mock_k8s_api.list_namespaced_pod.return_value = type(
        "R", (), {"items": [running_pod]}
    )()
    result = pods.find_pods_by_name("database", namespace="default")
    assert "No pods" in result


def test_find_pods_error_returns_string(mock_k8s_api):
    mock_k8s_api.list_namespaced_pod.side_effect = RuntimeError("no cluster")
    result = pods.find_pods_by_name("web", namespace="default")
    assert result.startswith("Failed to list pods")
    assert "no cluster" in result


def test_diagnose_crashloop_uses_previous_logs(mock_k8s_api, crash_pod):
    mock_k8s_api.read_namespaced_pod.return_value = crash_pod
    mock_k8s_api.read_namespaced_pod_log.return_value = "\n".join(
        f"line {i}" for i in range(100)
    )
    result = diagnose.diagnose_pod_crash(
        "web-app-7f9d4b8c6-x2k1p", namespace="default"
    )
    assert "CrashLoopBackOff" in result
    assert "restarts: 7" in result
    assert "Pre-crash logs" in result
    # Tail is capped at 40 lines (default).
    assert "line 60" in result and "line 0" not in result
    assert "line 59" not in result
    mock_k8s_api.read_namespaced_pod_log.assert_called_once()
    assert mock_k8s_api.read_namespaced_pod_log.call_args.kwargs["previous"] is True


def test_diagnose_running_pod_current_logs(mock_k8s_api, running_pod):
    mock_k8s_api.read_namespaced_pod.return_value = running_pod
    mock_k8s_api.read_namespaced_pod_log.return_value = "ok"
    result = diagnose.diagnose_pod_crash("web-app-7f9d4b8c6-abcde")
    assert "state: running" in result
    assert "### Logs (tail 40)" in result
    assert mock_k8s_api.read_namespaced_pod_log.call_args.kwargs.get("previous") is None


def test_diagnose_error_returns_string(mock_k8s_api):
    mock_k8s_api.read_namespaced_pod.side_effect = RuntimeError("not found")
    result = diagnose.diagnose_pod_crash("missing-pod", namespace="default")
    assert result.startswith("Failed to diagnose pod")
    assert "not found" in result


def test_handshake_no_cluster_contact(mock_k8s_api):
    result = health.handshake()
    assert result.startswith("pong:")
    assert "uptime" in result
    mock_k8s_api.list_namespaced_pod.assert_not_called()


def test_cluster_status_reachable(mock_k8s_api, running_pod, crash_pod):
    mock_k8s_api.list_namespaced_pod.return_value = type(
        "R", (), {"items": [running_pod, crash_pod]}
    )()
    result = health.cluster_status(namespace="default")
    assert result == "cluster reachable: 2 pod(s) in namespace '" + "default" + "'."


def test_cluster_status_unreachable(mock_k8s_api):
    mock_k8s_api.list_namespaced_pod.side_effect = RuntimeError("no cluster")
    result = health.cluster_status(namespace="default")
    assert result.startswith("cluster unreachable:")
    assert "no cluster" in result
