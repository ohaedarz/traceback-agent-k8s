# kube-traceback

A read-only Model Context Protocol (MCP) server for diagnosing Kubernetes
clusters. It exposes two tools to any MCP client (Cursor, Claude Desktop,
Cline, etc.): fuzzy pod-name search and crash traceback retrieval.

The server is meant to bridge an AI assistant to a remote cluster (typically a
small `k3s` setup on a VPS) without ever mutating cluster state and without
dumping huge JSON blobs into the model's context window.

## What it does

Two tools are registered:

- `find_pods_by_name(search_keyword, namespace)` — fuzzy-matches a keyword
  (e.g. "web app") against pod names and labels in a namespace and returns a
  short table of matching pods and their phase.
- `diagnose_pod_crash(pod_name, namespace)` — reports container states,
  termination reasons and exit codes, plus a tailed log buffer. For pods in
  `CrashLoopBackOff` it fetches the *previous* container's logs so you see what
  happened right before the crash.

Both tools return compact Markdown strings and cap their output size. All calls
are read-only; anything that would mutate the cluster is out of scope.

## Requirements

- Python 3.12+
- A reachable Kubernetes cluster with a kubeconfig
  (local dev, a VPS host, or running inside the cluster)

## Setup

The project ships a `uv.lock` so every machine gets the exact same
dependencies (including the pinned `mcp<2` line). The recommended setup is `uv`:

```bash
uv sync --extra dev
# then activate the environment it creates (or call it directly)
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

If you prefer plain `pip` (without a lock file):

Create and activate a virtual environment, then install the package in
editable mode with dev dependencies. Make sure `mcp` and `kubernetes` are
installed before running — an uninstalled environment is the usual cause of
`ModuleNotFoundError: No module named 'mcp'`.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

## Configuration

The server reads its configuration from environment variables. Nothing about
the cluster is hardcoded.

| Variable | Purpose | Default |
|---|---|---|
| `KUBECONFIG` | Path to the kubeconfig file to use | in-cluster config, then `~/.kube/config` |
| `TRACEBACK_DEFAULT_NAMESPACE` | Namespace used when a tool argument is omitted | `default` |
| `TRACEBACK_LOG_TAIL_LINES` | Number of log lines returned by the crash tool | `40` |

How the client is resolved:

- If `KUBECONFIG` is set, that file is loaded.
- Otherwise the server tries in-cluster configuration (service account), which
  works when the server itself runs as a Pod.
- If in-cluster config is unavailable it falls back to `~/.kube/config`.

For a VPS `k3s` cluster, copy the kubeconfig to the machine running the server
and point `KUBECONFIG` at it:

```bash
# Linux / macOS
export KUBECONFIG=/home/you/.kube/config
# Windows
$env:KUBECONFIG = "$HOME\.kube\config"
```

## Running the server

Start it directly (it talks stdio, as MCP servers do):

```bash
python -m kubetraceback
# or, if the console script is on your PATH
kubetraceback
```

### Verifying with the MCP Inspector

The recommended way to check that the tools are registered and behave correctly
without an LLM in the loop is the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m kubetraceback
```

If you have `mcp` installed locally, `fastmcp dev` also works:

```bash
fastmcp dev src/kubetraceback/mcp_server.py
```

## Connecting from an MCP client

Add the server to your client's MCP configuration. Example for a stdio server:

```json
{
  "mcpServers": {
    "kube-traceback": {
      "command": "python",
      "args": ["-m", "kubetraceback"],
      "env": {
        "KUBECONFIG": "/absolute/path/to/kubeconfig",
        "TRACEBACK_DEFAULT_NAMESPACE": "default"
      }
    }
  }
}
```

Adjust `command` to match your environment; a full path to `python` is often
more reliable than a bare name.


## Running in Docker on a VPS

The server runs with two transports:

- `stdio` (default) — for local MCP clients that spawn the process directly.
- `http` (streamable-HTTP) — for networked access, which is what you want in a
  Docker container on a VPS.

### 1. Build the image

```bash
docker build -t kube-traceback .
```

### 2. Run with a mounted kubeconfig

For a standalone VPS (not inside a cluster), copy your kubeconfig onto the box
and mount it read-only:

```bash
docker run -d --name kube-traceback \
  -p 127.0.0.1:8765:8765 \
  -v /home/you/.kube/config:/home/agent/.kube/config:ro \
  -e KUBECONFIG=/home/agent/.kube/config \
  -e TRACEBACK_DEFAULT_NAMESPACE=default \
  -e TRACEBACK_LOG_TAIL_LINES=40 \
  kube-traceback
```

Or use the provided compose file (edit the `source:` path first):

```bash
# point the bind mount at your real kubeconfig, then:
docker compose up -d --build
```

The container listens on `127.0.0.1:8765/mcp` by default. Put your reverse
proxy (nginx, Caddy, Traefik) in front if you need to reach it from elsewhere.

### 3. Connect an MCP client over HTTP

Point any streamable-HTTP-capable client at the endpoint:

```
http://your-vps:8765/mcp
```

If the server is reachable only over your LAN/SSH tunnel, a quick tunnel works:

```bash
ssh -N -L 8765:127.0.0.1:8765 you@your-vps
# then use http://127.0.0.1:8765/mcp locally
```

### 4. Running inside the cluster instead

If you deploy the container as a Pod, you can drop the kubeconfig mount entirely:
give the Pod a read-only `ServiceAccount` and the server will use in-cluster
configuration automatically (it detects `KUBERNETES_SERVICE_HOST`). This is the
preferred setup in production.

## Typical workflow

When a user asks about a failing service, the assistant is expected to:

1. Call `find_pods_by_name` with a fuzzy keyword to get the exact pod name
   (Kubernetes appends a random hash suffix).
2. Call `diagnose_pod_crash` with that pod name to read termination reasons,
   exit codes, and the pre-crash log tail.

## Adding a new tool

Tools live in `src/kubetraceback/tools/`. Follow the existing pattern:

```python
from kubetraceback.tools import mcp
from kubetraceback import k8s_client


@mcp.tool()
def get_cluster_events(namespace: str = "default") -> str:
    """Fetches the last 10 cluster events to cross-reference with failing pods."""
    try:
        api = k8s_client.get_core_api()
        events = api.list_namespaced_event(namespace=namespace, limit=10)
        # Build a compact Markdown list and always return a plain string.
        return "..."
    except Exception as e:
        return f"Failed to retrieve cluster events: {e}"
```

Rules for any new tool:

- Always return a `str`.
- Always wrap the body in `try/except` and return an error string instead of
  raising, so one bad call never crashes the server.
- Cap the size of the result before returning it.
- Use `k8s_client.default_namespace()` for the default namespace.
- Keep it read-only.

After adding a module, import it from `src/kubetraceback/tools/__init__.py` so
its tools get registered.

## Testing

Tests run fully offline against a mocked Kubernetes client, so they pass with
no cluster and no network access:

```bash
pytest
```

Make sure you run the project's interpreter, not a system-wide `pytest`. If you
see `No module named 'mcp'` or `No module named 'kubernetes'`, you are running
`pytest` from the wrong environment. Use the venv explicitly:

```bash
# Windows
.venv\Scripts\python -m pytest
# macOS / Linux
.venv/bin/python -m pytest
```


If the `kubernetes` package is not installed, `tests/conftest.py` automatically
installs a minimal stub from `tests/stubs/kubernetes/` so the test suite can
still run.

## Project layout

```
src/
  kubetraceback/
    __init__.py
    __main__.py          # python -m kubetraceback
    mcp_server.py        # server entrypoint
    fastmcp_compat.py    # FastMCP (mcp 1.x) / MCPServer (mcp 2.x) shim
    k8s_client.py        # lazy client + env configuration
    tools/
      __init__.py        # creates the mcp instance, imports tool modules
      pods.py            # find_pods_by_name
      diagnose.py        # diagnose_pod_crash
tests/
  conftest.py            # offline fixtures
  mock_k8s.py            # mock pod factories
  test_tools.py
  stubs/                 # offline `kubernetes` stub
pyproject.toml
AGENTS.md                # guidance for AI coding agents working in this repo
```
