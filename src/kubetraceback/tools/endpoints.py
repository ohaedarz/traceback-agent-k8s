"""Service endpoint inspection (read-only): why a service has no backends."""

from kubetraceback.tools import mcp

from kubetraceback import auth, k8s_client

_MAX_ENDPOINTS = 20


def _service_table(services, ns: str) -> str:
    lines = ["| Service | Port(s) | Selector |", "|---|---|---|"]
    for svc in services:
        ports = ", ".join(
            f"{p.name or '?'}:{p.port}"
            for p in (svc.spec.ports or [])
        ) or "n/a"
        selector = ", ".join(f"{k}={v}" for k, v in (svc.spec.selector or {}).items()) or "None (no selector)"
        lines.append(f"| {svc.metadata.name} | {ports} | {selector} |")
    return "\n".join(lines)


@mcp.tool()
def list_services(namespace: str = "default") -> str:
    """
    Lists services in a namespace (read-only) with their ports and pod
    selectors. First step when debugging 'service exists but gets 503':
    compare the selector against actual pod labels.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        services = (
            k8s_client.get_core_api().list_namespaced_service(namespace=ns).items
        )
        if not services:
            return f"No services in namespace '{ns}'."
        return f"# Services in '{ns}'\n\n{_service_table(services, ns)}"
    except Exception as e:
        return f"Failed to list services in namespace '{namespace}': {e}"


@mcp.tool()
def get_service_endpoints(service: str, namespace: str = "default") -> str:
    """
    Reports the ready endpoints backing a service (read-only). A service with
    zero endpoints is the classic cause of 503/502 from an ingress. Also shows
    the service selector and (when endpoints exist) which pods back it.
    """
    try:
        api = k8s_client.get_core_api()
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        svc = api.read_namespaced_service(name=service, namespace=ns)
        selector = ", ".join(
            f"{k}={v}" for k, v in (svc.spec.selector or {}).items()
        ) or "None (no selector)"
        sections = [
            f"# Service '{service}' in '{ns}'",
            f"- selector: {selector}",
            f"- type: {svc.spec.type or 'ClusterIP'}",
        ]

        eps = api.read_namespaced_endpoints(name=service, namespace=ns)
        subsets = eps.subsets or []
        total = 0
        ready_addrs = []
        not_ready_addrs = []
        for subset in subsets:
            for addr in subset.addresses or []:
                total += 1
                target_ref = addr.target_ref or None
                pod_name = target_ref.name if target_ref is not None else "?"
                ready_addrs.append(f"{pod_name} ({addr.ip})")
            for addr in subset.not_ready_addresses or []:
                target_ref = addr.target_ref or None
                pod_name = target_ref.name if target_ref is not None else "?"
                not_ready_addrs.append(f"{pod_name} ({addr.ip})")

        sections.append(f"- ready endpoints: {total}")
        if ready_addrs:
            shown = ready_addrs[:_MAX_ENDPOINTS]
            sections.append("- ready: " + ", ".join(shown))
            if len(ready_addrs) > len(shown):
                sections.append(f"  ({len(ready_addrs) - len(shown)} more)")
        if not_ready_addrs:
            sections.append(
                "- NOT READY: " + ", ".join(not_ready_addrs[:_MAX_ENDPOINTS])
            )
        if total == 0:
            sections.append(
                "- **NO READY ENDPOINTS** - the service selector matches no "
                "Running/Ready pod. Check pod labels vs the selector above, "
                "and pod readiness."
            )
        return "\n".join(sections)
    except Exception as e:
        return f"Failed to read endpoints for service '{service}' in namespace '{namespace}': {e}"
