"""Pod describe: conditions, containers, probes, resources, volumes (read-only)."""

from kubetraceback import auth, k8s_client
from kubetraceback.tools import mcp

_MAX_CONDITIONS = 10


def _fmt_resources(resources) -> str:
    if resources is None:
        return "n/a"
    parts = []
    for section, attrs in (
        ("requests", resources.requests),
        ("limits", resources.limits),
    ):
        if attrs:
            parts.append(
                f"{section}: " + ", ".join(f"{k}={v}" for k, v in attrs.items())
            )
    return "; ".join(parts) or "n/a"


def _fmt_probe(probe) -> str:
    if probe is None:
        return "none"
    if probe.http_get is not None:
        kind = f"http {probe.http_get.path or '/'}"
    elif probe.tcp_socket is not None:
        kind = f"tcp :{probe.tcp_socket.port}"
    elif probe.exec_ is not None:
        kind = "exec " + " ".join(probe.exec_.command or [])
    else:
        kind = "unknown"
    extra = []
    if probe.initial_delay_seconds:
        extra.append(f"initialDelay={probe.initial_delay_seconds}")
    if probe.failure_threshold:
        extra.append(f"failures={probe.failure_threshold}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    return kind + suffix


def _usage_line(pod_name: str, ns: str) -> str | None:
    """Optional CPU/mem usage via metrics-server; None when unavailable."""
    try:
        from kubernetes import client

        metrics = client.MetricsApi().get_namespaced_pod(name=pod_name, namespace=ns)
        if not metrics or not metrics.containers:
            return None
        parts = []
        for c in metrics.containers:
            res = c.usage or {}
            bits = []
            if "cpu" in res:
                bits.append(f"cpu={res['cpu']}")
            if "memory" in res:
                bits.append(f"mem={res['memory']}")
            if bits:
                parts.append(f"{c.name}: {', '.join(bits)}")
        if not parts:
            return None
        return "- usage (metrics-server): " + "; ".join(parts)
    except Exception:
        return None


@mcp.tool()
def get_pod_describe(pod_name: str, namespace: str = "default") -> str:
    """
    Compact 'kubectl describe pod' (read-only): phase, conditions, container
    images/ports, readiness/liveness probe config, resource requests/limits,
    and unschedulable reasons. Use to understand WHY a pod is stuck, pending,
    or failing probes - before pulling logs.
    """
    try:
        api = k8s_client.get_core_api()
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        pod = api.read_namespaced_pod(name=pod_name, namespace=ns)
        status = pod.status
        sections = [f"# {pod.metadata.name} ({status.phase or 'Unknown'}) in '{ns}'"]

        # Node + start
        if status.host_ip:
            sections.append(f"- host IP: {status.host_ip}")
        if status.pod_ip:
            sections.append(f"- pod IP: {status.pod_ip}")

        # Conditions (the most diagnostic part for stuck pods)
        conditions = (status.conditions or [])[:_MAX_CONDITIONS]
        if conditions:
            sections.append("## Conditions")
            for cond in conditions:
                msg = (cond.message or "").strip().replace("\n", " ")
                if len(msg) > 200:
                    msg = msg[:197] + "..."
                line = f"- {cond.type}: {cond.status}"
                if cond.reason:
                    line += f" ({cond.reason})"
                if msg:
                    line += f" - {msg}"
                sections.append(line)

        # Containers
        spec_containers = (pod.spec.containers or []) if pod.spec else []
        if spec_containers:
            sections.append("## Containers")
            for c in spec_containers:
                sections.append(f"### {c.name}")
                sections.append(f"- image: {c.image}")
                if c.ports:
                    ports = ", ".join(str(p.container_port) for p in c.ports)
                    sections.append(f"- ports: {ports}")
                sections.append(f"- resources: {_fmt_resources(c.resources)}")
                sections.append(f"- liveness: {_fmt_probe(c.liveness_probe)}")
                sections.append(f"- readiness: {_fmt_probe(c.readiness_probe)}")
                if c.command:
                    sections.append(f"- command: {' '.join(c.command)}")

        # Init containers (brief)
        init_containers = (pod.spec.init_containers or []) if pod.spec else []
        if init_containers:
            sections.append("## Init containers")
            for c in init_containers:
                sections.append(f"- {c.name} ({c.image})")

        # Usage (optional, metrics-server)
        usage = _usage_line(pod_name, ns)
        if usage:
            sections.append(usage)

        return "\n\n".join(sections)
    except Exception as e:
        return f"Failed to describe pod '{pod_name}' in namespace '{namespace}': {e}"
