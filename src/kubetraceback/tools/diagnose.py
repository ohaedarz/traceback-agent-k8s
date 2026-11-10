"""Crash diagnosis: container lifecycle data + pre-crash log tails."""

from kubetraceback.tools import mcp

from kubetraceback import auth, k8s_client


def _tail(text: str, n: int) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:]) if len(lines) > n else (text or "")


@mcp.tool()
def diagnose_pod_crash(pod_name: str, namespace: str = "default") -> str:
    """
    Diagnose a crashing pod: reports container states, termination reasons,
    exit codes, and a tailed log buffer (previous container if CrashLooping).
    """
    try:
        api = k8s_client.get_core_api()
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        tail = k8s_client.log_tail_lines()
        pod = api.read_namespaced_pod(name=pod_name, namespace=ns)
        sections = [f"# {pod.metadata.name} ({pod.status.phase})"]
        container_statuses = pod.status.container_statuses or []
        if not container_statuses:
            sections.append("No container statuses reported.")
        crashing = pod.status.phase == "CrashLoopBackOff" or any(
            (cs.state.waiting is not None and cs.state.waiting.reason == "CrashLoopBackOff")
            for cs in container_statuses
        )
        for cs in container_statuses:
            state = cs.state
            terminated = state.terminated
            waiting = state.waiting
            sections.append(f"## Container: {cs.name}")
            if terminated:
                sections.append(
                    f"- state: terminated (exit code {terminated.exit_code}, "
                    f"reason {terminated.reason})"
                )
            elif waiting:
                sections.append(
                    f"- state: waiting (reason {waiting.reason}, "
                    f"message {waiting.message or 'n/a'})"
                )
            else:
                sections.append("- state: running")
            if cs.restart_count:
                sections.append(f"- restarts: {cs.restart_count}")
            log_kwargs = {"name": pod_name, "container": cs.name}
            if crashing:
                try:
                    logs = api.read_namespaced_pod_log(
                        namespace=ns, **log_kwargs, previous=True
                    )
                    sections.append(f"### Pre-crash logs (tail {tail})")
                except Exception:
                    logs = api.read_namespaced_pod_log(
                        namespace=ns, **log_kwargs
                    )
                    sections.append(f"### Current logs (tail {tail})")
            else:
                logs = api.read_namespaced_pod_log(namespace=ns, **log_kwargs)
                sections.append(f"### Logs (tail {tail})")
            sections.append(f"```\n{_tail(logs, tail)}\n```")
        return "\n\n".join(sections)
    except Exception as e:
        return f"Failed to diagnose pod '{pod_name}' in namespace '{namespace}': {e}"
