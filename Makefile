# Makefile for kube-traceback (kube-traceback MCP server).
#
# Targets are grouped for humans and for AI agents: the most common
# operations are one-liners. Keep commands idempotent where possible.

PYTHON        ?= python3
UV            ?= uv
PYTEST        := $(PYTHON) -m pytest
PIP           := $(PYTHON) -m pip
VENV          := .venv
PKGS          := $(PYTHON) -m py_compile \
                 src/kubetraceback/__init__.py \
                 src/kubetraceback/__main__.py \
                 src/kubetraceback/mcp_server.py \
                 src/kubetraceback/fastmcp_compat.py \
                 src/kubetraceback/k8s_client.py 
                 src/kubetraceback/config.py \
                 src/kubetraceback/tools/__init__.py \
                 src/kubetraceback/tools/pods.py \
                 src/kubetraceback/tools/diagnose.py

.DEFAULT_GOAL := help

## -- setup ---------------------------------------------------------------

## Create a virtualenv and install the package with dev deps.
install:
	$(PIP) install -e ".[dev]"

## Preferred install using uv. Syncs the exact locked environment from uv.lock
## into .venv (creates it if missing). Run `uv sync` on any fresh machine.
install-uv:
	$(UV) sync --extra dev

## Same as install-uv but forces a re-resolution (after changing pyproject.toml).
lock:
	$(UV) lock

## -- test ----------------------------------------------------------------

## Run the full test suite (offline, no cluster required).
test:
	$(PYTEST) -q

## Run tests with verbose output and coverage-friendly flags.
test-verbose:
	$(PYTEST) -v

## Run a single test file or node, e.g. make test-one TEST=tests/test_tools.py::test_diagnose_crashloop_uses_previous_logs
test-one:
	@test -n "$(TEST)" || (echo "Usage: make test-one TEST=<path or node id>"; exit 1)
	$(PYTEST) -q $(TEST)

## Compile-check all source files without running tests (fast sanity check).
compile:
	$(PKGS)
	@echo "compile OK"

## -- run -----------------------------------------------------------------

## Run the MCP server over stdio (default transport).
run:
	$(PYTHON) -m kubetraceback

## Run the MCP server over HTTP (streamable-http) for networked access.
run-http:
	TRACEBACK_MCP_TRANSPORT=http \
	TRACEBACK_MCP_HOST=${HOST:-127.0.0.1} \
	TRACEBACK_MCP_PORT=${PORT:-8765} \
	$(PYTHON) -m kubetraceback

## Verify the server with the MCP Inspector (requires Node.js).
inspect:
	npx @modelcontextprotocol/inspector $(PYTHON) -m kubetraceback

## -- docker --------------------------------------------------------------

## Build the Docker image.
docker-build:
	docker build -t kube-traceback .

## Run the container locally with a mounted kubeconfig (edit path as needed).
docker-run:
	docker run --rm -it -p 127.0.0.1:8765:8765 \
		-v ${KUBECONFIG_HOST_PATH:-$$HOME/.kube/config}:/home/agent/.kube/config:ro \
		-e KUBECONFIG=/home/agent/.kube/config \
		kube-traceback

## Start/stop the compose stack on the VPS.
docker-up:
	docker compose up -d --build
docker-down:
	docker compose down
docker-logs:
	docker compose logs -f

## -- quality -------------------------------------------------------------

## Syntax check only (fast, no deps).
lint: compile
	@echo "lint OK"

## -- clean ---------------------------------------------------------------

## Remove Python and build artifacts.
clean:
	rm -rf .pytest_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

## -- help ----------------------------------------------------------------

## Show this help.
help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
