"""Shared fixtures: a mocked CoreV1Api and a config snapshot so tests run
fully offline with no environment requirements."""

from unittest import mock

import importlib.util
import os
import sys

import pytest


def _use_stub_kubernetes_if_unavailable():
    """Install a stub kubernetes module if the real one is not installed."""
    try:
        import kubernetes  # noqa: F401
    except ImportError:
        stubs = os.path.join(os.path.dirname(__file__), "stubs")
        spec = importlib.util.spec_from_file_location(
            "kubernetes", os.path.join(stubs, "kubernetes", "__init__.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["kubernetes"] = module
        spec.loader.exec_module(module)
        # Register the client submodule so "from kubernetes.client import models" works.
        client_spec = importlib.util.spec_from_file_location(
            "kubernetes.client", os.path.join(stubs, "kubernetes", "client.py")
        )
        client_module = importlib.util.module_from_spec(client_spec)
        client_spec.loader.exec_module(client_module)
        sys.modules["kubernetes.client"] = client_module
        module.client = client_module


_use_stub_kubernetes_if_unavailable()

from kubetraceback import config, k8s_client  # noqa: E402


def _test_config(**overrides):
    """A stdio/no-auth config snapshot; tests override fields as needed."""
    base = dict(
        transport="stdio",
        default_namespace="default",
        log_tail_lines=40,
        kubeconfig=None,
        in_cluster=None,
        keycloak_issuer_url=None,
    )
    base.update(overrides)
    return config._Config(base)


@pytest.fixture(autouse=True)
def app_config(monkeypatch):
    """Every test starts with a stdio/no-auth config snapshot (CODE_STYLES.md:
    config is loaded at init, tools only read the snapshot)."""
    monkeypatch.setattr(config, "CONFIG", _test_config())
    yield config.CONFIG


@pytest.fixture(autouse=True)
def mock_k8s_api(monkeypatch):
    """Patch k8s_client._core_api with a MagicMock for every test."""
    fake_api = mock.MagicMock(name="CoreV1Api")
    monkeypatch.setattr(k8s_client, "_core_api", fake_api)
    yield fake_api
    monkeypatch.setattr(k8s_client, "_core_api", None)


def make_list_pods_response(pods):
    return mock.MagicMock(items=pods)


@pytest.fixture
def crash_pod():
    from tests import mock_k8s

    return mock_k8s.make_pod(
        "web-app-7f9d4b8c6-x2k1p",
        phase="CrashLoopBackOff",
        labels={"app": "web-app"},
        restarts=7,
        waiting_reason="CrashLoopBackOff",
    )


@pytest.fixture
def running_pod():
    from tests import mock_k8s

    return mock_k8s.make_pod(
        "web-app-7f9d4b8c6-abcde",
        phase="Running",
        labels={"app": "web-app"},
    )