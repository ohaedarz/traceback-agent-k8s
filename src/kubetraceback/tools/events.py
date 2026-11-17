"""Cluster and pod event retrieval (read-only)."""

from kubetraceback import auth, k8s_client
from kubetraceback.tools import mcp

_MAX_EVENTS = 20
_MAX_MESSAGE_CHARS = 200


def _format_event(event) -> str:
    involved = event.involved_object or None
    target = ""
    if involved is not None:
        target = f"{involved.kind or '?'}/{involved.name or '?'}"
    reason = event.reason or "?"
    message = (event.message or "").strip().replace("\n", " ")
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 3] + "..."
    count = f" x{event.count}" if (event.count or 0) > 1 else ""
    ts = (event.last_timestamp or event.event_time or "") or "n/a"
    return f"- **{reason}**{count} [{ts}] {target}: {message}"


@mcp.tool()
def get_cluster_events(namespace: str | None = None) -> str:
    """
    Fetches the last cluster lifecycle events in a namespace (read-only) to
    cross-reference with failing pods: scheduling, image pull, probe, and
    restart events. Returns at most 20 most recent events.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        events = (
            k8s_client.get_core_api()
            .list_namespaced_event(namespace=ns, limit=_MAX_EVENTS, _continue=None)
            .items
        )
        if not events:
            return f"No recent events in namespace '{ns}'."
        events = sorted(
            events,
            key=lambda e: e.last_timestamp or e.event_time or "",
            reverse=True,
        )
        sections = [f"# Events in '{ns}' ({len(events)} most recent)"]
        sections += [_format_event(e) for e in events]
        return "\n".join(sections)
    except Exception as e:
        return f"Failed to retrieve cluster events in namespace '{namespace}': {e}"


@mcp.tool()
def get_pod_events(pod_name: str, namespace: str | None = None) -> str:
    """
    Fetches recent events for a single pod (read-only): OOMKilled,
    BackOff, FailedScheduling, image pull and probe failures. Use to
    narrow down WHY a specific pod is failing before pulling logs.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)

        events = (
            k8s_client.get_core_api()
            .list_namespaced_event(
                namespace=ns,
                field_selector=f"involvedObject.name={pod_name},involvedObject.kind=Pod",
                limit=_MAX_EVENTS,
            )
            .items
        )
        if not events:
            return f"No recent events for pod '{pod_name}' in namespace '{ns}'."
        events = sorted(
            events,
            key=lambda e: e.last_timestamp or e.event_time or "",
            reverse=True,
        )
        sections = [f"# Events for pod '{pod_name}' ({len(events)} most recent)"]
        sections += [_format_event(e) for e in events]
        return "\n".join(sections)
    except Exception as e:
        return f"Failed to retrieve events for pod '{pod_name}' in namespace '{namespace}': {e}"
