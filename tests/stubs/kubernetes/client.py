"""Offline stub of `kubernetes.client` (models used by tests).

Mirrors the required-field behavior of the real SDK's pydantic models closely
enough that tests pass identically whether the real `kubernetes` package or
this stub is in use.
"""


def _model_factory(name, required=()):
    def __init__(self, **kwargs):
        for field in required:
            if field not in kwargs:
                raise TypeError(f"{name}.__init__ missing required field: {field!r}")
        # Default every known field to None so attribute access never fails.
        for attr in (
            "name", "labels", "phase", "container_statuses",
            "restart_count", "image", "image_id", "ready",
            "state", "waiting", "terminated", "reason", "message", "exit_code",
        ):
            if attr not in kwargs:
                setattr(self, attr, None)
        for key, value in kwargs.items():
            setattr(self, key, value)

    return type(name, (), {"__init__": __init__})


class CoreV1Api:  # placeholder; tests replace it with a MagicMock
    def __init__(self, *args, **kwargs):
        raise RuntimeError("stub CoreV1Api should be mocked in tests")


class _Models:
    pass


_specs = {
    "V1Pod": (),
    "V1ObjectMeta": (),
    "V1PodStatus": (),
    "V1ContainerStatus": ("name", "image", "image_id", "ready", "restart_count"),
    "V1ContainerState": (),
    "V1ContainerStateWaiting": (),
    "V1ContainerStateTerminated": ("exit_code",),
}

for _name, _req in _specs.items():
    setattr(_Models, _name, _model_factory(_name, _req))

models = _Models()
