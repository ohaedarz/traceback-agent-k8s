"""Offline mock factories for kubernetes API responses.

These build real ``kubernetes.client.models`` objects, so the tool code under
test exercises the same attribute access it would against a live cluster. The
factories always supply every field the real SDK requires.
"""

from kubernetes.client import models as k8s_models


def make_pod(
    name: str,
    phase: str = "Running",
    labels: dict | None = None,
    restarts: int = 0,
    waiting_reason: str | None = None,
    exit_code: int | None = None,
    terminated_reason: str | None = None,
) -> k8s_models.V1Pod:
    metadata = k8s_models.V1ObjectMeta(name=name, labels=labels or {})

    waiting = (
        k8s_models.V1ContainerStateWaiting(reason=waiting_reason, message="boom")
        if waiting_reason
        else None
    )
    terminated = (
        k8s_models.V1ContainerStateTerminated(
            exit_code=exit_code if exit_code is not None else 1,
            reason=terminated_reason or "Error",
        )
        if (exit_code is not None or terminated_reason is not None)
        else None
    )
    state = k8s_models.V1ContainerState(waiting=waiting, terminated=terminated)

    container_status = k8s_models.V1ContainerStatus(
        name=name,
        restart_count=restarts,
        image="example/app:latest",
        image_id="sha256:example",
        ready=phase == "Running",
        state=state,
    )

    return k8s_models.V1Pod(
        metadata=metadata,
        status=k8s_models.V1PodStatus(
            phase=phase, container_statuses=[container_status]
        ),
    )
