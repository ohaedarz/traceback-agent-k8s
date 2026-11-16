"""Offline tests for the kube-traceback tools."""

from kubetraceback.tools import describe, diagnose, endpoints, events, health, pods
from tests import mock_k8s


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
    result = diagnose.diagnose_pod_crash("web-app-7f9d4b8c6-x2k1p", namespace="default")
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


# ---------------------------------------------------------------------------
# get_cluster_events
# ---------------------------------------------------------------------------


def test_cluster_events_lists_recent(mock_k8s_api):
    e1 = mock_k8s.make_event(
        reason="BackOff", target="p1", count=5, last_timestamp="2026-11-10T10:00:00Z"
    )
    e2 = mock_k8s.make_event(
        reason="Pulled", target="p1", last_timestamp="2026-11-10T09:00:00Z"
    )
    mock_k8s_api.list_namespaced_event.return_value = type(
        "R", (), {"items": [e2, e1]}
    )()
    result = events.get_cluster_events(namespace="default")
    assert "# Events in 'default'" in result
    assert "BackOff" in result and "Pulled" in result
    # most recent (BackOff) is listed before older (Pulled)
    assert result.index("BackOff") < result.index("Pulled")
    assert " x5" in result  # count surfaced


def test_cluster_events_empty(mock_k8s_api):
    mock_k8s_api.list_namespaced_event.return_value = type("R", (), {"items": []})()
    result = events.get_cluster_events(namespace="default")
    assert result.startswith("No recent events")


def test_cluster_events_error_returns_string(mock_k8s_api):
    mock_k8s_api.list_namespaced_event.side_effect = RuntimeError("no cluster")
    result = events.get_cluster_events(namespace="default")
    assert result.startswith("Failed to retrieve cluster events")
    assert "no cluster" in result


def test_cluster_events_caps_at_20(mock_k8s_api):
    items = [
        mock_k8s.make_event(name=f"e{i}", last_timestamp=f"2026-11-10T10:0{i % 10}:00Z")
        for i in range(50)
    ]
    mock_k8s_api.list_namespaced_event.return_value = type("R", (), {"items": items})()
    events.get_cluster_events(namespace="default")
    # request is limited server-side; assert we asked for limit=20
    assert mock_k8s_api.list_namespaced_event.call_args.kwargs.get("limit") == 20


# ---------------------------------------------------------------------------
# get_pod_events
# ---------------------------------------------------------------------------


def test_pod_events_field_selector(mock_k8s_api):
    e = mock_k8s.make_event(reason="OOMKilling", target="my-pod")
    mock_k8s_api.list_namespaced_event.return_value = type("R", (), {"items": [e]})()
    result = events.get_pod_events("my-pod", namespace="default")
    assert "OOMKilling" in result
    fs = mock_k8s_api.list_namespaced_event.call_args.kwargs.get("field_selector")
    assert "involvedObject.name=my-pod" in fs
    assert "involvedObject.kind=Pod" in fs


def test_pod_events_empty(mock_k8s_api):
    mock_k8s_api.list_namespaced_event.return_value = type("R", (), {"items": []})()
    result = events.get_pod_events("my-pod", namespace="default")
    assert result.startswith("No recent events for pod 'my-pod'")


def test_pod_events_error_returns_string(mock_k8s_api):
    mock_k8s_api.list_namespaced_event.side_effect = RuntimeError("boom")
    result = events.get_pod_events("my-pod", namespace="default")
    assert result.startswith("Failed to retrieve events for pod 'my-pod'")


# ---------------------------------------------------------------------------
# list_services
# ---------------------------------------------------------------------------


def test_list_services_table(mock_k8s_api):
    svc = mock_k8s.make_service(name="web", selector={"app": "web"})
    mock_k8s_api.list_namespaced_service.return_value = type(
        "R", (), {"items": [svc]}
    )()
    result = endpoints.list_services(namespace="default")
    assert "# Services in 'default'" in result
    assert "| web |" in result
    assert "app=web" in result
    assert "http:80" in result


def test_list_services_no_selector(mock_k8s_api):
    svc = mock_k8s.make_service(name="ext", selector={})
    mock_k8s_api.list_namespaced_service.return_value = type(
        "R", (), {"items": [svc]}
    )()
    result = endpoints.list_services(namespace="default")
    assert "None (no selector)" in result


def test_list_services_empty(mock_k8s_api):
    mock_k8s_api.list_namespaced_service.return_value = type("R", (), {"items": []})()
    result = endpoints.list_services(namespace="default")
    assert result.startswith("No services")


def test_list_services_error_returns_string(mock_k8s_api):
    mock_k8s_api.list_namespaced_service.side_effect = RuntimeError("down")
    result = endpoints.list_services(namespace="default")
    assert result.startswith("Failed to list services")


# ---------------------------------------------------------------------------
# get_service_endpoints
# ---------------------------------------------------------------------------


def test_service_endpoints_ready(mock_k8s_api):
    mock_k8s_api.read_namespaced_service.return_value = mock_k8s.make_service(
        name="web", selector={"app": "web"}
    )
    mock_k8s_api.read_namespaced_endpoints.return_value = mock_k8s.make_endpoints(
        name="web", ready=[("web-abc", "10.42.0.5"), ("web-def", "10.42.0.6")]
    )
    result = endpoints.get_service_endpoints("web", namespace="default")
    assert "- selector: app=web" in result
    assert "ready endpoints: 2" in result
    assert "web-abc (10.42.0.5)" in result


def test_service_endpoints_not_ready_listed(mock_k8s_api):
    mock_k8s_api.read_namespaced_service.return_value = mock_k8s.make_service(
        name="web"
    )
    mock_k8s_api.read_namespaced_endpoints.return_value = mock_k8s.make_endpoints(
        name="web", ready=[], not_ready=[("web-abc", "10.42.0.5")]
    )
    result = endpoints.get_service_endpoints("web", namespace="default")
    assert "NOT READY" in result
    assert "web-abc" in result


def test_service_endpoints_zero_flagged(mock_k8s_api):
    mock_k8s_api.read_namespaced_service.return_value = mock_k8s.make_service(
        name="web"
    )
    mock_k8s_api.read_namespaced_endpoints.return_value = mock_k8s.make_endpoints(
        name="web"
    )
    result = endpoints.get_service_endpoints("web", namespace="default")
    assert "NO READY ENDPOINTS" in result
    assert "ready endpoints: 0" in result


def test_service_endpoints_error_returns_string(mock_k8s_api):
    mock_k8s_api.read_namespaced_service.side_effect = RuntimeError("not found")
    result = endpoints.get_service_endpoints("missing", namespace="default")
    assert result.startswith("Failed to read endpoints for service 'missing'")


# ---------------------------------------------------------------------------
# get_pod_describe
# ---------------------------------------------------------------------------


def test_pod_describe_shows_conditions_and_containers(mock_k8s_api):
    pod = mock_k8s.make_rich_pod(
        "stuck-pod",
        phase="Pending",
        conditions=[
            mock_k8s.make_condition(
                "PodScheduled",
                "False",
                "Unschedulable",
                "0/3 nodes available: 3 Insufficient cpu",
            ),
        ],
    )
    mock_k8s_api.read_namespaced_pod.return_value = pod
    result = describe.get_pod_describe("stuck-pod", namespace="default")
    assert "# stuck-pod (Pending)" in result
    assert "## Conditions" in result
    assert "PodScheduled: False" in result
    assert "Unschedulable" in result
    assert "Insufficient cpu" in result
    assert "## Containers" in result
    assert "liveness: http /healthz" in result
    assert "limits: cpu=500m, memory=256Mi" in result


def test_pod_describe_running_no_conditions(mock_k8s_api):
    pod = mock_k8s.make_rich_pod("ok-pod", phase="Running")
    mock_k8s_api.read_namespaced_pod.return_value = pod
    result = describe.get_pod_describe("ok-pod", namespace="default")
    assert "# ok-pod (Running)" in result
    assert "## Conditions" not in result


def test_pod_describe_error_returns_string(mock_k8s_api):
    mock_k8s_api.read_namespaced_pod.side_effect = RuntimeError("not found")
    result = describe.get_pod_describe("missing", namespace="default")
    assert result.startswith("Failed to describe pod 'missing'")


# ---------------------------------------------------------------------------
# diagnose_pod_crash: restart history line
# ---------------------------------------------------------------------------


def test_diagnose_includes_last_termination(mock_k8s_api):
    from kubernetes.client import models as m

    last_term = m.V1ContainerStateTerminated(exit_code=137, reason="OOMKilled")
    last_state = m.V1ContainerState(terminated=last_term)
    pod = mock_k8s.make_pod(
        "oom-pod",
        phase="CrashLoopBackOff",
        restarts=3,
        waiting_reason="CrashLoopBackOff",
    )
    pod.status.container_statuses[0].last_state = last_state
    mock_k8s_api.read_namespaced_pod.return_value = pod
    mock_k8s_api.read_namespaced_pod_log.return_value = "out of memory"
    result = diagnose.diagnose_pod_crash("oom-pod", namespace="default")
    assert "- restarts: 3" in result
    assert "last termination:" in result
    assert "exit code 137" in result
    assert "OOMKilled" in result


def test_diagnose_no_last_state_no_history_line(mock_k8s_api, crash_pod):
    mock_k8s_api.read_namespaced_pod.return_value = crash_pod
    mock_k8s_api.read_namespaced_pod_log.return_value = "x"
    result = diagnose.diagnose_pod_crash("web-app-7f9d4b8c6-x2k1p", namespace="default")
    assert "last termination:" not in result
