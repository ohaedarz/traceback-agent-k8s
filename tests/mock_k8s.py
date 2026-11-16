"""Offline mock factories for kubernetes API responses.

These build real ``kubernetes.client.models`` objects, so the tool code under
test exercises the same attribute access it would against a live cluster.
Model classes are resolved via :func:`_model`, which tolerates SDK versions
that prefix Core models with ``Core`` (kubernetes>=35 ships ``CoreV1Event``).
"""

from kubernetes.client import models as k8s_models


def _model(name):
    """Return a model class by name, falling back to the ``Core``-prefixed form."""
    if hasattr(k8s_models, name):
        return getattr(k8s_models, name)
    return getattr(k8s_models, "Core" + name)


def make_pod(
    name,
    phase="Running",
    labels=None,
    restarts=0,
    waiting_reason=None,
    exit_code=None,
    terminated_reason=None,
):
    metadata = _model("V1ObjectMeta")(name=name, labels=labels or {})

    waiting = (
        _model("V1ContainerStateWaiting")(reason=waiting_reason, message="boom")
        if waiting_reason
        else None
    )
    terminated = (
        _model("V1ContainerStateTerminated")(
            exit_code=exit_code if exit_code is not None else 1,
            reason=terminated_reason or "Error",
        )
        if (exit_code is not None or terminated_reason is not None)
        else None
    )
    state = _model("V1ContainerState")(waiting=waiting, terminated=terminated)

    container_status = _model("V1ContainerStatus")(
        name=name,
        restart_count=restarts,
        image="example/app:latest",
        image_id="sha256:example",
        ready=(phase == "Running"),
        state=state,
    )

    return _model("V1Pod")(
        metadata=metadata,
        status=_model("V1PodStatus")(
            phase=phase, container_statuses=[container_status]
        ),
    )


def make_rich_pod(
    name,
    phase="Pending",
    conditions=None,
    image="example/app:latest",
    cpu_limit="500m",
    mem_limit="256Mi",
):
    metadata = _model("V1ObjectMeta")(name=name, labels={"app": name})

    http_probe = _model("V1Probe")(
        http_get=_model("V1HTTPGetAction")(path="/healthz", port=8080),
        initial_delay_seconds=5,
        failure_threshold=3,
    )

    container = _model("V1Container")(
        name=name,
        image=image,
        ports=[_model("V1ContainerPort")(container_port=8080)],
        resources=_model("V1ResourceRequirements")(
            limits={"cpu": cpu_limit, "memory": mem_limit},
            requests={"cpu": "100m", "memory": "64Mi"},
        ),
        readiness_probe=http_probe,
        liveness_probe=http_probe,
    )
    spec = _model("V1PodSpec")(containers=[container])

    return _model("V1Pod")(
        metadata=metadata,
        spec=spec,
        status=_model("V1PodStatus")(
            phase=phase,
            conditions=conditions or [],
            pod_ip="10.42.0.99",
            host_ip="10.42.0.1",
        ),
    )


def make_condition(cond_type, status, reason="", message=""):
    return _model("V1PodCondition")(
        type=cond_type, status=status, reason=reason, message=message
    )


def make_event(
    name="evt",
    kind="Pod",
    target="some-pod",
    reason="BackOff",
    message="back-off restarting failed container",
    count=1,
    last_timestamp="2026-11-10T10:00:00Z",
):
    return _model("V1Event")(
        metadata=_model("V1ObjectMeta")(name=name),
        involved_object=_model("V1ObjectReference")(kind=kind, name=target),
        reason=reason,
        message=message,
        count=count,
        last_timestamp=last_timestamp,
    )


def make_service(name="web", selector=None, ports=None, svc_type="ClusterIP"):
    if ports is None:
        ports = [_model("V1ServicePort")(name="http", port=80, protocol="TCP")]
    return _model("V1Service")(
        metadata=_model("V1ObjectMeta")(name=name),
        spec=_model("V1ServiceSpec")(
            selector=(selector if selector is not None else {"app": name}),
            ports=ports,
            type=svc_type,
        ),
    )


def make_endpoints(name="web", ready=None, not_ready=None):
    ready = ready if ready is not None else []
    not_ready = not_ready if not_ready is not None else []
    addresses = [
        _model("V1EndpointAddress")(
            ip=ip, target_ref=_model("V1ObjectReference")(kind="Pod", name=pod)
        )
        for pod, ip in ready
    ]
    not_ready_addresses = [
        _model("V1EndpointAddress")(
            ip=ip, target_ref=_model("V1ObjectReference")(kind="Pod", name=pod)
        )
        for pod, ip in not_ready
    ]
    subsets = []
    if addresses or not_ready_addresses:
        subsets = [
            _model("V1EndpointSubset")(
                addresses=addresses, not_ready_addresses=not_ready_addresses
            )
        ]
    return _model("V1Endpoints")(
        metadata=_model("V1ObjectMeta")(name=name), subsets=subsets
    )
