"""Offline stub of the `kubernetes` package for sandboxed test runs.

Provides just enough surface (client.CoreV1Api, config loaders, V1 models)
for the tool tests to run without network access. In a real environment the
actual `kubernetes` distribution is used instead.
"""

import importlib.util
import os
import sys as _sys


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


# Load the stub `kubernetes.client` module (models + CoreV1Api placeholder) so
# `from kubernetes.client import models` and `from kubernetes import client`
# both work offline.
_here = os.path.dirname(os.path.abspath(__file__))
_client_spec = importlib.util.spec_from_file_location(
    "kubernetes.client", os.path.join(_here, "client.py")
)
client = importlib.util.module_from_spec(_client_spec)
_sys.modules["kubernetes.client"] = client
_client_spec.loader.exec_module(client)
