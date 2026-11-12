# AGENTS.md — kube-traceback MCP Server

This repository implements a **Model Context Protocol (MCP)** server (Python + `mcp` SDK, `FastMCP`) that acts as a **read-only diagnostic bridge to a Kubernetes cluster** (primary target: an `k3s` cluster on a VPS).

The server must run in **any** deployment setting: local dev, CI, or a VPS/k8s environment. Configuration comes from environment variables and a kubeconfig path — never hardcode cluster addresses, namespaces, or credentials in code.

---

## Architecture & Security Scope

1. **Read-only / diagnostic boundary.** The server is strictly for monitoring, fuzzy searching, and traceback retrieval. Do **not** implement mutations or deletions (`kubectl delete`, `helm uninstall`, patch/apply/scale) without explicit human approval and strict schema validation.
2. **No secrets in context.** Never expose raw `kubeconfig` contents or cluster tokens into logs, tool results, or the chat context window. All cluster interaction must go through the official Python SDK (`kubernetes.client`), loading the kubeconfig via `kubernetes.config.load_kube_config` (or in-cluster config when running inside k8s).
3. **Context efficiency.** Kubernetes returns massive JSON. Filter, truncate, or slice output **before** returning it to the MCP context. Tools must return compact, human-readable strings (Markdown), never raw API objects.
4. **Never crash the server.** Wrap every tool body in `try/except` and return a descriptive error string instead of raising, so one bad call doesn't kill the server process.

## Configuration (portable across environments)

| Env var | Purpose | Fallback |
|---|---|---|
| `KUBECONFIG` | Path to kubeconfig file (VPS k3s: `~/.kube/config` copied over, or mounted secret) | in-cluster config when running as a Pod |
| `TRACEBACK_DEFAULT_NAMESPACE` | Default namespace when a tool arg is omitted | `default` |
| `TRACEBACK_LOG_TAIL_LINES` | Lines of log tail returned by log tools | `40` |

- When running **outside** a cluster (local dev / VPS host): use `kubernetes.config.load_kube_config()` with `~/.kube/config`.
- When running **inside** the cluster (this repo's `traceback-agent-k8s` deployment): use `kubernetes.config.load_incluster_config()` if `KUBECONFIG` is unset.
- Lazy-load the client (first tool call), not at import time, so `main.py` can start without a reachable cluster.
- **One config module** (CODE_STYLES.md): all environment variables are read exactly once in kubetraceback/config.py::init() at app start; no other module may read the environment. Missing required vars fail fast with ConfigError.

## Tool Schemas (active & target)

### 1. `find_pods_by_name(search_keyword: str, namespace: str = "default")`
- **Purpose:** Resolve conversational queries ("my web app") to exact randomized pod names.
- **Behavior:** List pods in the namespace, lowercase fuzzy-match on pod name/labels, return a compact table of matching `pod_name` + `phase` (limit results to ~10).
- **Agent guideline:** Run this **first** to obtain the exact `pod_name` hash string before pulling logs.

### 2. `diagnose_pod_crash(pod_name: str, namespace: str = "default")`
- **Purpose:** Extract container lifecycle data and stack-trace blocks.
- **Behavior:**
  - Inspect `pod.status.container_statuses` for termination reasons and exit codes.
  - If the pod is in `CrashLoopBackOff`, call the log API with `previous=True` to get pre-crash logs.
  - Tail exactly `TRACEBACK_LOG_TAIL_LINES` (default 40) lines to prevent token bloat.
  - Return a compact Markdown summary: container, state, reason, exit code, then the log tail.

### 3. `get_cluster_events(namespace: str = "default")`
- **Purpose:** Cross-reference failing pods with cluster lifecycle events.
- **Behavior:** List the most recent events (capped at 20), sorted newest-first, as a compact `reason [timestamp] kind/name: message` list. Messages are truncated to ~200 chars.

### 4. `get_pod_events(pod_name: str, namespace: str = "default")`
- **Purpose:** Narrow down WHY a specific pod is failing before pulling logs.
- **Behavior:** Filter events by `involvedObject.name=<pod>,involvedObject.kind=Pod` (server-side field selector), newest-first, capped at 20. Surfaces OOMKilled, BackOff, FailedScheduling, image-pull and probe failures.

### 5. `get_pod_describe(pod_name: str, namespace: str = "default")`
- **Purpose:** Compact `kubectl describe pod` for stuck/pending pods.
- **Behavior:** Phase, pod/host IP, conditions (most diagnostic for Unschedulable / probe failures), per-container image, ports, resource requests/limits, and liveness/readiness probe config. Optionally appends a metrics-server usage line when available (degrades silently when metrics-server is absent).

### 6. `list_services(namespace: str = "default")`
- **Purpose:** First step in "service exists but returns 503" debugging.
- **Behavior:** Compact table of services with ports and pod selectors so the agent can compare the selector against actual pod labels.

### 7. `get_service_endpoints(service: str, namespace: str = "default")`
- **Purpose:** Prove whether a service has ready backends.
- **Behavior:** Reports the service selector, ready endpoint count + pod names, any NOT-READY addresses, and an explicit `NO READY ENDPOINTS` flag (the classic 503/502 cause).

- **Agent guideline:** Typical crash-debug flow is `find_pods_by_name` -> `get_pod_events` (or `get_cluster_events`) -> `diagnose_pod_crash` -> `get_pod_describe`. For "route returns 5xx" use `list_services` -> `get_service_endpoints`.

## Extension Pattern (new tools)

Use this exact template for any new diagnostic tool:

```python
@mcp.tool()
def get_cluster_events(namespace: str = "default") -> str:
    """
    Fetches the last 10 cluster lifecycle events to cross-reference with failing pods.
    """
    # 1. Catch exceptions locally so the server process does not crash
    try:
        events = api.list_namespaced_event(namespace=namespace, limit=10)
        # 2. Slice and format compactly into a clean Markdown list
        # 3. Always return a plain string to the MCP context
        return ...
    except Exception as e:
        return f"Failed to retrieve cluster events: {str(e)}"
```

Rules: always return `str`, always catch exceptions, always cap result size, default `namespace` from `TRACEBACK_DEFAULT_NAMESPACE`.

## Testing & Verification

1. **Verify tool contracts with the MCP Inspector, not raw LLM chat:**
   ```bash
   fastmcp dev src/kubetraceback/mcp_server.py
   # or
   npx @modelcontextprotocol/inspector python -m kubetraceback
   ```
2. **Mock the cluster** when the remote VPS is unreachable: see `tests/mock_k8s.py`, which intercepts `kubernetes.client` calls with `unittest.mock` so tests run offline:
   ```bash
   pytest
   ```
   When the `kubernetes` package itself is unavailable, `tests/conftest.py` installs the stub from `tests/stubs/kubernetes/` automatically.
3. Never point tests at a real cluster; tests must pass with no network access.

## Project Layout

`/src`-based layout, importable as package `kubetraceback`:

```
pyproject.toml           # deps: mcp[cli], kubernetes, pytest (dev)
src/
  kubetraceback/
    __init__.py
    __main__.py          # python -m kubetraceback -> mcp.run()
    mcp_server.py        # FastMCP instance, mcp.run() entrypoint
    fastmcp_compat.py    # FastMCP (mcp 1.x) / MCPServer (mcp 2.x) shim
    k8s_client.py        # lazy kubeconfig/in-cluster client loader
    tools/
      __init__.py        # creates the mcp instance, imports tool modules
      pods.py            # find_pods_by_name
      diagnose.py        # diagnose_pod_crash
      events.py          # get_cluster_events, get_pod_events
      describe.py        # get_pod_describe
      endpoints.py       # list_services, get_service_endpoints
tests/
  conftest.py            # offline mock fixtures for kubernetes.client
  mock_k8s.py            # mock API response factories
  test_tools.py
  stubs/                 # offline `kubernetes` package stub (no-network test runs)
```

- All project code lives under `src/kubetraceback/`; `tests/` sits at the repo root.
- `pyproject.toml` uses a standard build backend with src-layout package discovery so `pip install -e .` works.
- MCP entrypoint: `python -m kubetraceback` (or an installed `kubetraceback` console-script).
  - MCP Inspector: `fastmcp dev src/kubetraceback/mcp_server.py`.

## Definition of Done for a Change

- [ ] Tool returns a compact `str` and caps its size.
- [ ] Tool catches all exceptions and returns an error string.
- [ ] No secrets or raw kubeconfig content in any output.
- [ ] No mutating k8s API calls introduced.
- [ ] `pytest` passes offline (mocked client).
- [ ] Verified in MCP Inspector (`fastmcp dev src/kubetraceback/mcp_server.py`).
- [ ] New code placed under `src/kubetraceback/`, tests under `tests/`.
