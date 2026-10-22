"""Offline stub of the `kubernetes` package for sandboxed test runs.

Provides just enough surface (client.CoreV1Api, config loaders, V1 models)
for the tool tests to run without network access. In a real environment the
actual `kubernetes` distribution is used instead.
"""


class ConfigException(Exception):
    pass


class _Config:
    @staticmethod
    def load_kube_config(*args, **kwargs):
        pass

    @staticmethod
    def load_incluster_config(*args, **kwargs):
        raise ConfigException("stub: no in-cluster config")


config = _Config()


class _Client:
    class CoreV1Api:  # placeholder; tests replace it with a MagicMock
        def __init__(self, *args, **kwargs):
            raise RuntimeError("stub CoreV1Api should be mocked in tests")


class _ClientNamespace:
    """Namespace so `from kubernetes.client import models` works."""

    CoreV1Api = _Client.CoreV1Api


client = _ClientNamespace()


def _model_factory(name):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    cls = type(name, (), {"__init__": __init__})
    return cls


class _Models:
    pass


for _name in (
    "V1Pod",
    "V1ObjectMeta",
    "V1PodStatus",
    "V1ContainerStatus",
    "V1ContainerState",
    "V1ContainerStateWaiting",
    "V1ContainerStateTerminated",
):
    setattr(_Models, _name, _model_factory(_name))

models = _Models()
