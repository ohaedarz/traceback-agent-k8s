# AGENTS.md — kube-traceback MCP Server

This repository implements a **Model Context Protocol (MCP)** server (Python + `mcp` SDK, `FastMCP`) that acts as a **read-only diagnostic bridge to a Kubernetes cluster** (primary target: an `k3s` cluster on a VPS).

The server must run in **any** deployment setting: local dev, CI, or a VPS/k8s environment. Configuration comes from environment variables and a kubeconfig path — never hardcode cluster addresses, namespaces, or credentials in code.

---

## Architecture & Security Scope

1. **Read-only / diagnostic boundary.** The server is strictly for monitoring, fuzzy searching, and traceback retrieval. Do **not** implement mutations or deletions (`kubectl delete`, `helm uninstall`, patch/apply/scale) without explicit human approval and strict schema validation.
2. **No secrets in context.** Never expose raw `kubeconfig` contents or cluster tokens into logs, tool results, or the chat context window. All cluster interaction must go through the official Python SDK (`kubernetes.client`), loading the kubeconfig via `kubernetes.config.load_kube_config` (or in-cluster config when running inside k8s).
3. **Context efficiency.** Kubernetes returns massive JSON. Filter, truncate, or slice output **before** returning it to the MCP context. Tools must return compact, human-readable strings (Markdown), never raw API objects.
4. **Never crash the server.** Wrap every tool body in `try/except` and return a descriptive error string instead of raising, so one bad call doesn't kill the server process.
5. **Namespace authorization.** When auth is enabled, every tool resolves the target namespace and calls `auth.check_namespace(ns)` **before** any Kubernetes API call. A user without a matching `ns:<name>` (or `ns:*`) role gets a `NamespaceNotAllowed` error string, not a cluster read.

## Configuration (portable across environments)

All environment variables are read **exactly once** in `kubetraceback/config.py::init()` at app start; no other module may read the environment. Missing required vars fail fast with `ConfigError`.

| Env var | Purpose | Fallback |
|---|---|---|
| `KUBECONFIG` | Path to kubeconfig file (VPS k3s: `~/.kube/config` copied over, or mounted secret) | in-cluster config when running as a Pod |
| `TRACEBACK_DEFAULT_NAMESPACE` | Default namespace when a tool arg is omitted | `default` |
| `TRACEBACK_LOG_TAIL_LINES` | Lines of log tail returned by log tools | `40` |
| `TRACEBACK_MCP_TRANSPORT` | `stdio` (default) or `http` | `stdio` |
| `TRACEBACK_MCP_HOST` / `TRACEBACK_MCP_PORT` | HTTP bind host/port (required when transport is `http`) | none (required) |
| `TRACEBACK_MCP_ALLOWED_HOSTS` / `TRACEBACK_MCP_ALLOWED_ORIGINS` | DNS-rebinding allow-lists for the HTTP transport (required when `http`) | none (required) |
| `KEYCLOAK_ISSUER_URL` | Realm issuer; setting this **enables auth** | disabled (stdio stays unauthenticated) |
| `KEYCLOAK_CLIENT_ID` | Expected MCP client id / audience | required when auth is on |
| `KEYCLOAK_REQUIRED_SCOPE` | Required token scope (e.g. `k8s:readonly`) | required when auth is on |
| `KEYCLOAK_NS_ROLE_PREFIX` | Prefix for namespace roles | `ns:` |
| `MCP_RESOURCE_URL` | Public MCP endpoint (resource indicator) | required when auth is on |

- When running **outside** a cluster (local dev / VPS host): use `kubernetes.config.load_kube_config()` with `~/.kube/config`.
- When running **inside** the cluster (this repo's `traceback-agent-k8s` deployment): use `kubernetes.config.load_incluster_config()` if `KUBECONFIG` is unset.
- Lazy-load the client (first tool call), not at import time, so the entrypoint can start without a reachable cluster.
- Only the two AGENTS.md-mandated fallbacks are provided: `TRACEBACK_DEFAULT_NAMESPACE` -> `default` and `TRACEBACK_LOG_TAIL_LINES` -> `40`. Everything else is required when the relevant feature (http / auth) is enabled.

## Authentication & Namespace RBAC

Optional Keycloak OAuth2 (OIDC) for the **HTTP** transport only; the stdio transport is always local and unauthenticated. See `AUTH.md` for the full Keycloak setup.

- Enabled when `KEYCLOAK_ISSUER_URL` is set (`src/kubetraceback/auth.py`).
- `KeycloakTokenVerifier` validates bearer JWTs (RS256) against the realm JWKS endpoint (cached; refetched once on an unknown `kid` for key rotation) and checks `iss`, `aud`/`resource`, and `exp`.
- Namespace authorization is **role-based**: Keycloak client roles on the MCP client named `ns:<namespace>` grant read access to that namespace, and `ns:*` grants every namespace. Realm roles with the same naming are honored too. A user without a matching role has no access.
- `build_auth_kwargs()` returns the FastMCP constructor kwargs enabling auth, or `{}` when auth is off.

## Tool Schemas (active)

All tools take `namespace: str | None = None`; when omitted, the namespace is read from `TRACEBACK_DEFAULT_NAMESPACE` via `k8s_client.default_namespace()`. Each tool resolves the namespace, calls `auth.check_namespace(ns)`, then does its read-only work inside a `try/except` that returns an error string.

### 1. `find_pods_by_name(search_keyword: str, namespace: str | None = None)`
- **Purpose:** Resolve conversational queries ("my web app") to exact randomized pod names.
- **Behavior:** List pods in the namespace, lowercase fuzzy-match on pod name/labels, return a compact table of matching `pod_name` + `phase` (limit results to ~10).
- **Agent guideline:** Run this **first** to obtain the exact `pod_name` hash string before pulling logs.

### 2. `diagnose_pod_crash(pod_name: str, namespace: str | None = None)`
- **Purpose:** Extract container lifecycle data and stack-trace blocks.
- **Behavior:**
  - Inspect `pod.status.container_statuses` for termination reasons and exit codes.
  - If the pod is in `CrashLoopBackOff`, call the log API with `previous=True` to get pre-crash logs (falls back to current logs if the previous container has no logs).
  - Tail up to `TRACEBACK_LOG_TAIL_LINES` (default 40) lines; shorter logs are returned whole.
  - Return a compact Markdown summary: container, state, restarts, last-termination line, then the log tail.

### 3. `get_cluster_events(namespace: str | None = None)`
- **Purpose:** Cross-reference failing pods with cluster lifecycle events.
- **Behavior:** List the most recent events (capped at 20), sorted newest-first, as a compact `- **reason** x<count> [timestamp] kind/name: message` list. Messages are truncated to ~200 chars; the repeat count is appended when > 1.

### 4. `get_pod_events(pod_name: str, namespace: str | None = None)`
- **Purpose:** Narrow down WHY a specific pod is failing before pulling logs.
- **Behavior:** Filter events by `involvedObject.name=<pod>,involvedObject.kind=Pod` (server-side field selector), newest-first, capped at 20. Surfaces OOMKilled, BackOff, FailedScheduling, image-pull and probe failures.

### 5. `get_pod_describe(pod_name: str, namespace: str | None = None)`
- **Purpose:** Compact `kubectl describe pod` for stuck/pending pods.
- **Behavior:** Phase, pod/host IP, conditions (most diagnostic for Unschedulable / probe failures), per-container image, ports, resource requests/limits, and liveness/readiness probe config. Also lists init containers and optionally appends a metrics-server usage line (degrades silently when metrics-server is absent).

### 6. `list_services(namespace: str | None = None)`
- **Purpose:** First step in "service exists but returns 503" debugging.
- **Behavior:** Compact table of services with ports and pod selectors so the agent can compare the selector against actual pod labels.

### 7. `get_service_endpoints(service: str, namespace: str | None = None)`
- **Purpose:** Prove whether a service has ready backends.
- **Behavior:** Reports the service selector and type, ready endpoint count + pod names, any NOT-READY addresses, and an explicit `NO READY ENDPOINTS` flag (the classic 503/502 cause).

### 8. `handshake()` (health)
- **Purpose:** Liveness smoke test.
- **Behavior:** Returns instance id, Python version, and uptime **without contacting the cluster**.

### 9. `cluster_status(namespace: str | None = None)` (health)
- **Purpose:** Connectivity check.
- **Behavior:** Lists pods in a namespace (read-only) to verify the kubeconfig/in-cluster connection works, returning pod count or an error string.

- **Agent guideline:** Typical crash-debug flow is `find_pods_by_name` -> `get_pod_events` (or `get_cluster_events`) -> `diagnose_pod_crash` -> `get_pod_describe`. For "route returns 5xx" use `list_services` -> `get_service_endpoints`.

## Extension Pattern (new tools)

Use this exact template for any new diagnostic tool:

```python
@mcp.tool()
def get_cluster_events(namespace: str | None = None) -> str:
    """
    Fetches the last 20 cluster lifecycle events to cross-reference with failing pods.
    """
    try:
        ns = namespace or k8s_client.default_namespace()
        auth.check_namespace(ns)
        # 1. Read-only k8s call through the shared, lazily-loaded client
        events = k8s_client.get_core_api().list_namespaced_event(
            namespace=ns, limit=_MAX_EVENTS
        ).items
        # 2. Slice and format compactly into a clean Markdown list
        # 3. Always return a plain string to the MCP context
        return ...
    except Exception as e:
        return f"Failed to retrieve cluster events: {str(e)}"
```

Rules: always return `str`, always catch exceptions, always cap result size, default `namespace` from `TRACEBACK_DEFAULT_NAMESPACE`, and call `auth.check_namespace(ns)` before any API call.

## Testing & Verification

1. **Verify tool contracts with the MCP Inspector, not raw LLM chat:**
   ```bash
   npx @modelcontextprotocol/inspector python -m kubetraceback
   # or the Makefile shortcut:
   make inspect
   ```
2. **Mock the cluster** when the remote VPS is unreachable: see `tests/mock_k8s.py`, which intercepts `kubernetes.client` calls with `unittest.mock` so tests run offline:
   ```bash
   pytest
   ```
   When the `kubernetes` package itself is unavailable, `tests/conftest.py` installs the stub from `tests/stubs/kubernetes/` automatically. The root `conftest.py` adds `src/` to `sys.path` so the package is importable even without `pip install -e .`.
3. Never point tests at a real cluster; tests must pass with no network access.

Run these from the repository root before handing off a Python change (match what CI runs):

```bash
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev pytest -q
```

## Project Layout

`/src`-based layout, importable as package `kubetraceback`:

```
pyproject.toml           # deps: mcp[cli], kubernetes; dev: pytest, ruff
conftest.py              # adds src/ to sys.path so pytest works without install
src/
  kubetraceback/
    __init__.py          # __version__
    __main__.py          # python -m kubetraceback -> config.init() + mcp.run()
    mcp_server.py        # transport setup (stdio / http) + /health route
    fastmcp_compat.py    # FastMCP (mcp 1.x) / MCPServer (mcp 2.x) shim
    config.py            # single env-read module; config.init() at startup
    auth.py              # Keycloak JWT verification + namespace RBAC
    k8s_client.py        # lazy kubeconfig/in-cluster client loader + default_namespace()
    tools/
      __init__.py        # creates the mcp instance, imports tool modules
      pods.py            # find_pods_by_name
      diagnose.py        # diagnose_pod_crash
      events.py          # get_cluster_events, get_pod_events
      describe.py        # get_pod_describe
      endpoints.py       # list_services, get_service_endpoints
      health.py          # handshake, cluster_status
tests/
  conftest.py            # offline mock fixtures + config snapshot
  mock_k8s.py            # mock API response factories
  test_tools.py          # offline tests for all diagnostic tools
  test_auth.py           # offline tests for JWT verification + namespace RBAC
  test_config.py         # offline tests for startup configuration
  stubs/                 # offline `kubernetes` package stub (no-network test runs)
```

- All project code lives under `src/kubetraceback/`; `tests/` sits at the repo root.
- `pyproject.toml` uses a standard build backend with src-layout package discovery so `pip install -e .` works.
- MCP entrypoint: `python -m kubetraceback` (or an installed `kubetraceback` console-script).

## Definition of Done for a Change

- [ ] Tool returns a compact `str` and caps its size.
- [ ] Tool catches all exceptions and returns an error string.
- [ ] Tool resolves the namespace and calls `auth.check_namespace(ns)` before any API call.
- [ ] No secrets or raw kubeconfig content in any output.
- [ ] No mutating k8s API calls introduced.
- [ ] `ruff check`, `ruff format --check`, and `pytest` all pass offline (mocked client).
- [ ] New code placed under `src/kubetraceback/`, tests under `tests/`.
