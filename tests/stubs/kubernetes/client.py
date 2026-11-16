"""Offline stub of `kubernetes.client` (models used by tests).

Mirrors the required-field behavior of the real SDK closely enough that tests
pass identically whether the real `kubernetes` package or this stub is in use.
"""

# Field names every model may expose; defaulting them to None means attribute
# access in the tools never raises AttributeError in offline tests.
_KNOWN_ATTRS = (
    # pod / meta
    "name",
    "labels",
    "phase",
    "namespace",
    "uid",
    "kind",
    # pod spec / status
    "spec",
    "status",
    "conditions",
    "host_ip",
    "pod_ip",
    "start_time",
    # containers
    "containers",
    "init_containers",
    "image",
    "image_id",
    "ready",
    "restart_count",
    "ports",
    "resources",
    "liveness_probe",
    "readiness_probe",
    "command",
    "args",
    # container state
    "state",
    "last_state",
    "waiting",
    "terminated",
    "running",
    "reason",
    "message",
    "exit_code",
    "signal",
    "started_at",
    "finished_at",
    # probes / actions
    "http_get",
    "tcp_socket",
    "exec_",
    "path",
    "port",
    "initial_delay_seconds",
    "period_seconds",
    "failure_threshold",
    "success_threshold",
    "timeout_seconds",
    # resources
    "requests",
    "limits",
    "cpu",
    "memory",
    # conditions
    "type",
    "status_",
    "observed_generation",
    "last_transition_time",
    # events
    "involved_object",
    "count",
    "first_timestamp",
    "last_timestamp",
    "event_time",
    "series",
    "source",
    "type_",
    # service / endpoints
    "selector",
    "type_",
    "cluster_ip",
    "ports_",
    "addresses",
    "not_ready_addresses",
    "subsets",
    "target_ref",
    "ip",
    "node_name",
    "hostname",
    # ports
    "container_port",
    "protocol",
)


def _model_factory(name, required=()):
    def __init__(self, **kwargs):
        for field in required:
            if field not in kwargs:
                raise TypeError(f"{name}.__init__ missing required field: {field!r}")
        for attr in _KNOWN_ATTRS:
            if attr not in kwargs:
                setattr(self, attr, None)
        for key, value in kwargs.items():
            setattr(self, key, value)

    return type(name, (), {"__init__": __init__})


class CoreV1Api:  # placeholder; tests replace it with a MagicMock
    def __init__(self, *args, **kwargs):
        raise RuntimeError("stub CoreV1Api should be mocked in tests")


class MetricsApi:  # placeholder; may be mocked for usage tests
    def __init__(self, *args, **kwargs):
        raise RuntimeError("stub MetricsApi should be mocked in tests")


class _Models:
    pass


_specs = {
    "V1Pod": (),
    "V1ObjectMeta": (),
    "V1PodStatus": (),
    "V1PodSpec": (),
    "V1Container": (),
    "V1ContainerStatus": ("name", "image", "image_id", "ready", "restart_count"),
    "V1ContainerState": (),
    "V1ContainerStateWaiting": (),
    "V1ContainerStateTerminated": ("exit_code",),
    "V1ContainerStateRunning": (),
    "V1PodCondition": (),
    "V1Probe": (),
    "V1HTTPGetAction": (),
    "V1TCPSocketAction": (),
    "V1ExecAction": (),
    "V1ResourceRequirements": (),
    "V1ObjectReference": (),
    "V1Event": (),
    "V1Service": (),
    "V1ServiceSpec": (),
    "V1ServicePort": (),
    "V1ContainerPort": (),
    "V1Endpoints": (),
    "V1EndpointSubset": (),
    "V1EndpointAddress": (),
}

for _name, _req in _specs.items():
    setattr(_Models, _name, _model_factory(_name, _req))

setattr(_Models, "CoreV1Event", _model_factory("CoreV1Event"))
models = _Models()
