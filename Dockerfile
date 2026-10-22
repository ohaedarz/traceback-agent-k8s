# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install the package and its runtime dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Non-root user. The kubeconfig is mounted read-only into this home dir.
RUN useradd --create-home --uid 10001 agent

# Default to the HTTP transport so the container is reachable across the
# network. Override at run time for stdio or a different port/host.
ENV TRACEBACK_MCP_TRANSPORT=http \
    TRACEBACK_MCP_HOST=0.0.0.0 \
    TRACEBACK_MCP_PORT=8765

# Light healthcheck against the HTTP transport.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/mcp', timeout=4)" || exit 1

USER agent

EXPOSE 8765

ENTRYPOINT ["kubetraceback"]
