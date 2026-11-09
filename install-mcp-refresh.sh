#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  printf '%s\n' 'Run this installer as root on the MCP host.' >&2
  exit 1
fi

MCP_HOME=${MCP_HOME:-/opt/k8s-mcp}
READER_SSH_USER=${READER_SSH_USER:-mcp-reader}
destination=/usr/local/sbin/refresh-k8s-mcp

temporary=$(mktemp "${destination}.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
{
  printf '%s\n' '#!/usr/bin/python3 -I'
  python3 - "$MCP_HOME" "$READER_SSH_USER" <<'SETTINGS'
import sys

print(f"MCP_HOME = {sys.argv[1]!r}")
print(f"DEFAULT_SSH_USER = {sys.argv[2]!r}")
SETTINGS
  cat <<'PY'
import base64
import fcntl
import hashlib
import json
import os
import ssl
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(MCP_HOME)
KEY = ROOT / "reader_ed25519"
HOSTS = ROOT / "known_hosts"
CONF = ROOT / "refresh.json"
MCP_KUBECONFIG_GID = 10001
MIN_REMAINING = 15 * 60
VALIDATE_GRACE = 20 * 60
os.umask(0o077)

def atomic_write(path, data: bytes, *, read_gid=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".refresh-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            if read_gid is not None:
                os.fchown(output.fileno(), 0, read_gid)
                os.fchmod(output.fileno(), 0o640)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def token_expiry(token: str) -> int:
    part = token.split(".")[1]
    return int(json.loads(base64.urlsafe_b64decode(
        part + "=" * (-len(part) % 4)))["exp"])

def read_kubeconfig(path: Path):
    text = path.read_text()
    user_block = [line.split(None, 1)[1].strip() for line in text.splitlines()
                  if line.strip().startswith("token: ")]
    server = [line.split(None, 1)[1].strip() for line in text.splitlines()
              if line.strip().startswith("server: ")][0]
    return server, user_block[0] if user_block else ""
with (ROOT / "refresh.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    conf = json.loads(CONF.read_text())
    kc = Path(conf["kubeconfig_file"])
    digest = ""
    pending = False
    if kc.exists():
        data = kc.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            fresh = token_expiry(read_kubeconfig(kc)[1]) - time.time() > MIN_REMAINING
        except Exception:
            fresh = False
        applied = (ROOT / "applied.sha256").exists() and \
            (ROOT / "applied.sha256").read_text().strip() == digest
        if fresh and applied:
            print("Kubernetes credential still valid; no rotation needed")
            raise SystemExit(0)
        pending = fresh and not applied

    if not pending:
        ssh_user = conf.get("ssh_user") or DEFAULT_SSH_USER
        ssh = ["ssh", "-nT", "-F", "/dev/null", "-i", str(KEY), "-p", str(int(conf["ssh_port"])),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + str(HOSTS),
               "-o", "ConnectTimeout=15", "-l", ssh_user, "--", conf["ssh_host"]]
        result = subprocess.run(ssh, stdout=subprocess.PIPE, text=True, timeout=90)
        if result.returncode != 0:
            raise SystemExit(f"Credential helper SSH failed (exit {result.returncode})")
        values = json.loads(result.stdout)
        if token_expiry(values["K8S_TOKEN"]) - time.time() < VALIDATE_GRACE:
            raise SystemExit("New token expires too soon; check token policy")
        if values["K8S_SERVICE_ACCOUNT"] != conf["expected_service_account"]:
            raise SystemExit("Unexpected ServiceAccount in credential response")
        for field in ("K8S_SERVER", "K8S_CA_DATA", "K8S_TOKEN", "K8S_SERVICE_ACCOUNT"):
            value = values[field]
            if not isinstance(value, str) or not value or any(c.isspace() for c in value):
                raise SystemExit("Invalid credential response field: " + field)
        if not values["K8S_SERVER"].startswith("https://"):
            raise SystemExit("Kubernetes URL must use HTTPS")
        # Verify CA, TLS name, bearer token, and pod-read permission before replacing.
        ca = base64.b64decode(values["K8S_CA_DATA"], validate=True).decode()
        context = ssl.create_default_context(cadata=ca)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context))
        request = urllib.request.Request(
            values["K8S_SERVER"].rstrip("/") + "/api/v1/pods?limit=1",
            headers={"Authorization": "Bearer " + values["K8S_TOKEN"]})
        with opener.open(request, timeout=30) as response:
            response.read()
        kc.parent.mkdir(parents=True, exist_ok=True)
        yaml = (
            "apiVersion: v1\n"
            "kind: Config\n"
            "clusters:\n"
            "- name: default\n"
            "  cluster:\n"
            "    server: " + values["K8S_SERVER"] + "\n"
            "    certificate-authority-data: " + values["K8S_CA_DATA"] + "\n"
            "contexts:\n"
            "- name: default\n"
            "  context:\n"
            "    cluster: default\n"
            "    user: default\n"
            "current-context: default\n"
            "users:\n"
            "- name: default\n"
            "  user:\n"
            "    token: " + values["K8S_TOKEN"] + "\n"
        )
        data = yaml.encode()
        digest = hashlib.sha256(data).hexdigest()
        atomic_write(kc, data, read_gid=MCP_KUBECONFIG_GID)

    # Replace the process so it loads the new file; a running client keeps its old token.
    compose = ["docker", "compose", "-f", conf["compose_file"],
               "-p", conf["compose_project"]]
    command = compose + ["up", "-d", "--no-deps", "--no-build",
                         "--force-recreate", conf["compose_service"]]
    compose_env = {**os.environ, "KUBECONFIG_FILE": str(kc)}
    subprocess.run(command, cwd=conf["compose_directory"], env=compose_env,
                   check=True, timeout=120)
    # Mark only after Compose succeeds; a failure is retried at the next tick.
    atomic_write(ROOT / "applied.sha256", (digest + "\n").encode())
    print("Kubernetes credential applied; MCP container recreated")
PY
} > "$temporary"

python3 - "$temporary" <<'CHECK'
import sys
from pathlib import Path

path = Path(sys.argv[1])
compile(path.read_bytes(), str(path), "exec")
CHECK

chown root:root "$temporary"
chmod 700 "$temporary"
mv -fT -- "$temporary" "$destination"
printf 'Installed %s\n' "$destination"
