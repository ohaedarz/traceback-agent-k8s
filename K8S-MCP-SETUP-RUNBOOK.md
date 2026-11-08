 Step-by-step: giving an external MCP server read-only, auto-renewing access to a Kubernetes cluster

This is a self-contained runbook. Run it top to bottom and the MCP server will
be able to observe the cluster with a short-lived, read-only credential that
refreshes itself. It is written for **any** cluster the MCP host can reach over
the network -- K3s, kubeadm, a distribution build, or a hosted control plane --
not for one specific cluster. Every machine-specific value is a variable, so the
same steps work unchanged for every target; a filled-in reference example
appears once, at the end.

The design this implements is in `K8S-ACCESS-DESIGN.md`. In brief: a dedicated
read-only ServiceAccount on the cluster, a root-owned helper that mints a short-
lived token for it, a restricted SSH channel the MCP host uses to reach that
helper (and nothing else), a kubeconfig file delivered to the MCP host, and a
systemd timer that renews the credential ahead of expiry and recreates the MCP
container so it picks it up.

## Prerequisites

Before starting, you need:

- The **cluster**: an administrator on a node that can talk to the cluster's own
  control plane (for K3s, a node with `k3s kubectl`; for other clusters, any
  account with `kubectl` plus the cluster's admin kubeconfig).
- The **MCP host**: the machine that runs the MCP server, with root, Python 3,
  an OpenSSH client, Docker Compose v2, and systemd. It can reach the cluster's
  API server over TLS.
- SSH access **from the MCP host to the cluster node** (a normal admin session,
  so you can create the restricted account in the next steps).
- The cluster's serving certificate already names an address that the MCP host
  can dial. For K3s this means that address is a `--tls-san`. Verify it, because
  the address the MCP dials must match a SAN or TLS validation fails:

  ```bash
  openssl s_client -connect CLUSTER_HOST:API_PORT </dev/null 2>/dev/null \
    | openssl x509 -noout -ext subjectAltName
  ```

- A firewall that lets the MCP host's IP reach the API port, and denies
  everything else. The certificate decides what a client can validate; the
  firewall decides who can connect. Confirm no earlier broad allow-rule defeats
  the intended restriction.

The MCP is read-only. It can get/list/watch pods, events, services, and
deployments, and get on pods/log. It cannot list namespaces beyond reading those
resources, read Secrets, exec, or write anything. That is enforced by the RBAC
you create below, not by the application.

## Variables

Set these once. Every block below is expressed in terms of them, so change only
these to point the runbook at a different cluster. The variables are grouped by
the host where the step that uses them runs: the **K8s node** (Steps 1-3) or the
**MCP host** (Steps 4-8). `CLUSTER` is just a label used in file names, so it
works on both.

K8s node -- used by Steps 1-3 (run these on the cluster node):

| Variable | Meaning | Example |
| --- | --- | --- |
| `NAMESPACE` | Namespace the read-only ServiceAccount lives in | `monitoring` |
| `READER_SA` | Name of the read-only ServiceAccount to create | `mcp-readonly` |
| `TOKEN_TTL` | Lifetime of each minted token | `1h` |
| `API_URL` | API server address the MCP host dials (in the TLS SAN) | `https://203.0.113.10:6443` |
| `HELPER_PATH` | Path of the credential helper on the cluster node | `/usr/local/sbin/kube-lab-mcp-credentials` |
| `HELPER_STATE_DIR` | Directory for helper config on the cluster node | `/etc/k8s-mcp` |
| `READER_SSH_USER` | Restricted SSH account on the cluster node | `mcp-reader` |

MCP host -- used by Steps 4-8 (run these on the MCP host):

| Variable | Meaning | Example |
| --- | --- | --- |
| `KUBECONFIG_DIR` | Directory on the MCP host for per-cluster kubeconfigs | `/opt/k8s-mcp/clusters` |
| `KUBECONFIG_FILE` | The MCP host's kubeconfig for this cluster | `/opt/k8s-mcp/clusters/k3s.yaml` |
| `SSH_HOST` | Cluster node host for the MCP host to reach | `203.0.113.10` |
| `SSH_PORT` | SSH port on the cluster node | `22` |
| `MCP_HOME` | Directory on the MCP host holding keys and refresh config | `/opt/k8s-mcp` |
| `COMPOSE_DIR` | Directory of the MCP's docker-compose file | `/opt/mcp` |
| `COMPOSE_FILE` | Name of the docker-compose file | `docker-compose.yml` |
| `COMPOSE_SERVICE` | The MCP service key in that file | `mcp` |
| `COMPOSE_PROJECT` | Compose project name (defaults to directory name) | `mcp` |

Shared (label, used in file names on both hosts):

| Variable | Meaning | Example |
| --- | --- | --- |
| `CLUSTER` | Short label for the target cluster (used in file names) | `k3s` |

The `READER_SA` and `NAMESPACE` pair is whatever the target cluster uses (or
you choose). Nothing in these steps depends on a particular name. If a different
identity already deploys to this cluster, the reader account is a separate one.

## Step 1 -- Create the read-only identity on the cluster  *(run on: K8s node)*

Run on the **cluster node**, as an administrator. Create the ServiceAccount, a
tightly scoped ClusterRole, and the ClusterRoleBinding that ties them together.

For K3s, prefix `kubectl` with `k3s`; for any other cluster, use your normal
`kubectl` and admin kubeconfig.

```bash
READER_SA="inventory-reader"
NAMESPACE="demo"

kubectl apply -f - <<YAML
apiVersion: v1
kind: ServiceAccount
metadata:
  name: "${READER_SA}"
  namespace: "${NAMESPACE}"
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: "${READER_SA}"
rules:
  - apiGroups: [""]
    resources: [pods, events, services]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [pods/log]
    verbs: [get]
  - apiGroups: [apps]
    resources: [deployments]
    verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: "${READER_SA}"
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: "${READER_SA}"
subjects:
  - kind: ServiceAccount
    name: "${READER_SA}"
    namespace: "${NAMESPACE}"
YAML
```

Replace the literal `READER_SA` and `NAMESPACE` placeholders with your values.
The `NAMESPACE` must already exist. If a role under this name existed before with
wider rules, this applies the read-only rules over it; also check that no other
binding grants this ServiceAccount more than intended.

Verify the identity can read and cannot write:

```bash
kubectl -n "$NAMESPACE" auth can-i get pods \
  --as="system:serviceaccount:${NAMESPACE}:${READER_SA}"

kubectl -n "$NAMESPACE" auth can-i create pods \
  --as="system:serviceaccount:${NAMESPACE}:${READER_SA}"
```

The first must print `yes`, the second `no`.

## Step 2 -- Install the credential helper on the cluster  *(run on: K8s node)*

The helper mints one short-lived token for `READER_SA` and emits the material the
MCP host needs to build its kubeconfig: the API server URL, the CA (read from the
cluster's own admin kubeconfig, so custom data paths work), the token, the
ServiceAccount name, and the token's API-issued expiry. It takes no arguments, so
a restricted channel can run exactly one command.

Run on the **cluster node**, as an administrator. The Python part is standard
library only (3.8+). The `K8S_KUBECTL` list is the one change for a non-K3s
cluster: it is the kubectl command plus the admin kubeconfig, with no arguments
of its own.

Copy install-mcp-helper.sh to the host and run:

```bash
sudo env \
  HELPER_STATE_DIR="$HELPER_STATE_DIR" \
  HELPER_PATH="$HELPER_PATH" \
  API_URL="$API_URL" \
  READER_SA="$READER_SA" \
  NAMESPACE="$NAMESPACE" \
  TOKEN_TTL="$TOKEN_TTL" \
  bash ./install-mcp-helper.sh
```

Alternatively, run on host:

```bash
install -d -m 755 "$HELPER_STATE_DIR"
printf '%s\n' "$API_URL" > "$HELPER_STATE_DIR/api-server"
chown root:root "$HELPER_STATE_DIR/api-server"
chmod 644 "$HELPER_STATE_DIR/api-server"

tee "$HELPER_PATH" >/dev/null <<SH
#!/bin/sh
exec /usr/bin/python3 -I - <<'PY'
import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

K8S_KUBECTL = ["/usr/local/bin/k3s", "kubectl", "--kubeconfig=/etc/rancher/k3s/k3s.yaml"]
STATE_DIR = Path("${HELPER_STATE_DIR}")
SA = "${READER_SA}"
NS = "${NAMESPACE}"
DURATION = "${TOKEN_TTL}"

def kube(*args):
    return subprocess.check_output(K8S_KUBECTL + list(args), text=True, timeout=30)

server = (STATE_DIR / "api-server").read_text().strip()
url = urlsplit(server)
if url.scheme != "https" or not url.hostname or url.username or url.password:
    raise SystemExit("Configure a valid HTTPS API URL in " + str(STATE_DIR / "api-server"))

admin = json.loads(kube("config", "view", "--minify", "-o", "json"))
ca = admin["clusters"][0]["cluster"]["certificate-authority-data"]
token = kube("-n", NS, "create", "token", SA, "--duration=" + DURATION).strip()

# Read the API-issued expiry; decoding is metadata inspection, not signature verification.
part = token.split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
expiry = int(claims["exp"])
if expiry - time.time() < 1200:
    raise SystemExit("Issued token lifetime is under 20 minutes; review server policy and timer intervals")

print(json.dumps({"K8S_SERVER": server, "K8S_CA_DATA": ca, "K8S_TOKEN": token,
                  "K8S_SERVICE_ACCOUNT": SA, "expires_at": expiry}))
PY
SH

chown root:root "$HELPER_PATH"
chmod 755 "$HELPER_PATH"
```

Replace the literal `HELPER_PATH`, `HELPER_STATE_DIR`, `API_URL`, `READER_SA`,
`NAMESPACE`, and `TOKEN_TTL` placeholders (and adjust `K8S_KUBECTL` for non-K3s).
The helper reads the CA from the cluster's own admin kubeconfig rather than
assuming a particular `serving-ca.crt` path.

Smoke-test it. It should print one JSON line; redirect the token out of view so
you do not display it:

```bash
HELPER_PATH >/dev/null && echo "helper OK"
```

## Step 3 -- Create the restricted SSH account on the cluster  *(run on: K8s node)*

### Create the MCP reader SSH account

Run this on the **cluster node** as root, with `READER_SSH_USER` and `HELPER_PATH` already defined. This creates the dedicated account if needed and prepares its `.ssh` directory.

```bash
if ! id "$READER_SSH_USER" >/dev/null 2>&1; then
  useradd --create-home --home-dir "/home/$READER_SSH_USER" \
    --shell /bin/sh "$READER_SSH_USER"
fi

reader_home=$(getent passwd "$READER_SSH_USER" | cut -d: -f6)

install -d -m 700 \
  -o "$READER_SSH_USER" \
  -g "$(id -gn "$READER_SSH_USER")" \
  "$reader_home/.ssh"
```

Generate the sudoers rule with the actual username and helper path.

```bash
printf '%s ALL=(root) NOPASSWD: %s ""\n' \
  "$READER_SSH_USER" "$HELPER_PATH"
```

Open the sudoers file.

```bash
visudo -f /etc/sudoers.d/k8s-mcp
```

Paste the **line printed by `printf`**, including the trailing `""`, then save and exit. The trailing `""` allows the helper to run without arguments only. Keep the helper, its parent directory, and `$HELPER_STATE_DIR` root-owned and unwritable by the reader account.

Verify that the reader can run the helper without a password. Redirect its output to keep the token out of the terminal.

```bash
sudo -u "$READER_SSH_USER" -- sudo -n "$HELPER_PATH" >/dev/null &&
  echo "Reader helper OK"
```


## Step 4 -- Install the MCP host's SSH key  *(run on: MCP host)*

On the **MCP host**, create the private key (do not overwrite an existing one)
and display the public key:

```bash
install -d -m 700 MCP_HOME
ssh-keygen -t ed25519 -N '' -C mcp-token-refresh -f MCP_HOME/reader_ed25519
cat MCP_HOME/reader_ed25519.pub
```

Copy that public key to the **cluster node**. In
`/home/READER_SSH_USER/.ssh/authorized_keys`, put the `restrict` options, the
forced command, and the key on a single line (replace `AAA...` with the real
public key):

```text
restrict,command="sudo -n HELPER_PATH" ssh-ed25519 AAA... mcp-token-refresh
```

Then fix ownership and permissions on the **cluster node**:

```bash
chown READER_SSH_USER:"$(id -gn READER_SSH_USER)" /home/READER_SSH_USER/.ssh/authorized_keys
chmod 600 /home/READER_SSH_USER/.ssh/authorized_keys
```

The key can only request reader credentials. It cannot open a shell, forward
ports, or run any other command. `restrict` requires an OpenSSH version that
supports that key option. If this account already has other unrestricted keys or
bindings, review them, because they would widen what the MCP host can do.

On the **MCP host**, seed `MCP_HOME/known_hosts` with the cluster node's
independently verified SSH host key. For a non-default port, the entry uses
`[hostname]:port`. Verify the fingerprint against the cluster's control plane or
provider console before trusting a scan. Set permissions:

```bash
chown root:root MCP_HOME/known_hosts
chmod 600 MCP_HOME/known_hosts
```

Confirm the restricted channel works end to end from the **MCP host**:

```bash
ssh -F /dev/null -i MCP_HOME/reader_ed25519 -p SSH_PORT \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=MCP_HOME/known_hosts \
  READER_SSH_USER@SSH_HOST >/dev/null && echo "channel OK"
```

If it prints `channel OK`, the channel is correct and it produced a credential.
Do not let that command's stdout into your shell history or a log.

## Step 5 -- Generate the MCP host's kubeconfig  *(run on: MCP host)*

The MCP server consumes a standard kubeconfig file. This first-run script calls
the helper over the restricted channel, validates the returned credential, and
writes `KUBECONFIG_FILE`.

Create `MCP_HOME/refresh.json` on the **MCP host** (host settings, not CI
variables). Use the same Compose project, file, and directory the MCP normally
runs with:

```bash
tee MCP_HOME/refresh.json >/dev/null <<'JSON'
{
  "ssh_host": "SSH_HOST",
  "ssh_port": SSH_PORT,
  "kubeconfig_file": "KUBECONFIG_FILE",
  "expected_service_account": "READER_SA",
  "compose_directory": "COMPOSE_DIR",
  "compose_file": "COMPOSE_FILE",
  "compose_project": "COMPOSE_PROJECT",
  "compose_service": "COMPOSE_SERVICE"
}
JSON
chmod 600 MCP_HOME/refresh.json
```

Install the refresh script on the **MCP host** (Python 3, OpenSSH client, and
Docker Compose v2 required). It runs as root because it recreates the Compose
service. Replace the literal `MCP_HOME` and `KUBECONFIG_DIR` placeholders; keep
the validation as-is.

```bash
tee /usr/local/sbin/refresh-k8s-mcp >/dev/null <<'PY'
#!/usr/bin/python3 -I
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

ROOT = Path("MCP_HOME")
CLUSTERS = Path("KUBECONFIG_DIR")
KEY = ROOT / "reader_ed25519"
HOSTS = ROOT / "known_hosts"
CONF = ROOT / "refresh.json"
MIN_REMAINING = 15 * 60
VALIDATE_GRACE = 20 * 60
os.umask(0o077)

def atomic_write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".refresh-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
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
        ssh = ["ssh", "-F", "/dev/null", "-i", str(KEY), "-p", str(int(conf["ssh_port"])),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + str(HOSTS),
               "-o", "ConnectTimeout=15", conf["ssh_host"]]
        result = subprocess.run(ssh, check=True, capture_output=True, text=True, timeout=90)
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
        atomic_write(kc, data)

    # Replace the process so it loads the new file; a running client keeps its old token.
    compose = ["docker", "compose", "-f", conf["compose_file"],
               "-p", conf["compose_project"]]
    command = compose + ["up", "-d", "--no-deps", "--no-build",
                         "--force-recreate", conf["compose_service"]]
    subprocess.run(command, cwd=conf["compose_directory"], check=True, timeout=120)
    # Mark only after Compose succeeds; a failure is retried at the next tick.
    atomic_write(ROOT / "applied.sha256", (digest + "\n").encode())
    print("Kubernetes credential applied; MCP container recreated")
PY
chown root:root /usr/local/sbin/refresh-k8s-mcp
chmod 700 /usr/local/sbin/refresh-k8s-mcp
```

The script writes the kubeconfig atomically and only after the new credential
has been validated against the live API. It then recreates the MCP container,
because a running client keeps the token it loaded at start and will not pick up
a new file. It does not print the token, and it bypasses HTTP proxies for the
direct cluster connection, so the MCP host must reach the API directly.

## Step 6 -- Point the Compose service at the kubeconfig file  *(run on: MCP host)*

Edit the MCP's Compose service so it loads `KUBECONFIG_FILE`. Preserve the image,
build, ports, volumes, and every other setting. Set `KUBECONFIG` to the file in
the service's `environment:`. `mcp` below is the Compose service key; replace it
consistently if yours differs.

```yaml
services:
  mcp:
    # Keep the existing image/build, ports, volumes, and other settings.
    environment:
      KUBECONFIG: KUBECONFIG_FILE
```

Remove any full-admin kubeconfig mount, any static `K8S_*` variables, and any
legacy kubeconfig the app might otherwise use, so this file is the only
credential source. Remember that `environment:` overrides `env_file`. This
fragment is not a complete Compose file.

## Step 7 -- First run and install the timer  *(run on: MCP host)*

On the **MCP host**, run the refresh script once. It fetches the initial
credential and starts the MCP with it:

```bash
/usr/local/sbin/refresh-k8s-mcp
```

If it succeeds, install the timer. It checks every five minutes and rotates when
fewer than fifteen minutes remain, then recreates the container. It does not
recreate on every check.

```bash
tee /etc/systemd/system/k8s-mcp-refresh.service >/dev/null <<'UNIT'
[Unit]
Description=Refresh MCP Kubernetes read-only credentials
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/local/sbin/refresh-k8s-mcp
TimeoutStartSec=300
UNIT

tee /etc/systemd/system/k8s-mcp-refresh.timer >/dev/null <<'UNIT'
[Unit]
Description=Check MCP Kubernetes credential expiry

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=10s
Unit=k8s-mcp-refresh.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now k8s-mcp-refresh.timer
systemctl start k8s-mcp-refresh.service
systemctl list-timers k8s-mcp-refresh.timer
journalctl -u k8s-mcp-refresh.service -n 30 --no-pager
```

When you deploy a new MCP image later, run the refresh service before starting
Compose so it loads a current credential. Never restore an old token from CI or a
`.env` afterward; a plain restart does not load new credential contents.

## Step 8 -- Verify  *(run on: MCP host)*

1. Use the MCP tools to list pods and read a pod's logs. A successful call proves
   the credential is valid and the RBAC allows it.
2. Check `systemctl list-timers k8s-mcp-refresh.timer` and the refresh service
   journal.
3. Confirm unattended renewal across at least one token lifetime: the journal
   should show a rotation followed by successful calls. A successful startup
   alone does not prove renewal works.
4. To force a rotation without waiting, delete `KUBECONFIG_FILE` and
   `MCP_HOME/applied.sha256`, then start the refresh service. The running
   container keeps its current credential until it is recreated. Do this only
   after the first successful run.

Read the current expiry without printing the token (on the **MCP host**):

```bash
python3 - <<'PY'
import base64, json
from datetime import datetime, timezone
from pathlib import Path
text = Path("KUBECONFIG_FILE").read_text()
token = [line.split(None, 1)[1].strip() for line in text.splitlines()
         if line.strip().startswith("token: ")][0]
part = token.split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
print("Reader token expires:", datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat())
PY
```

## Monitoring and operations notes

The kubeconfig file, the SSH private key, and the refresh config are
root-only. Do not commit them. The SSH key is the durable bootstrap credential;
deleting its `authorized_keys` line stops all future issuance. A token already
issued stays valid until it expires unless its ServiceAccount is deleted. Do not
delete a shared ServiceAccount to revoke a single client.

This rotation refreshes only the Kubernetes reader credential. It does not renew
application auth tokens, registry pull tokens, or any separate deploy identity.

If renewal cannot reach the cluster, the last good kubeconfig is preserved and
the running MCP keeps working until that token expires. No rotation method keeps
a credential valid while its source is unreachable.

## Troubleshooting

- **401 Unauthorized** -- token expired, wrong audience/cluster, or the
  ServiceAccount was recreated. Check the timer's journal and the actual expiry.
  Do not disable validation to mask it.
- **403 Forbidden** -- review the reader ClusterRole and ClusterRoleBinding and
  the resource being requested.
- **TLS error** -- verify `API_URL`, the certificate SANs, and the CA. The
  helper reads the current CA, so a rotated CA is picked up on the next rotation.
- **Connection refused or timeouts** -- inspect the API listener, routing, and
  firewall. These are not token-expiry evidence.
- **SSH failure** -- verify the reader key, `known_hosts` entry, the forced
  command, and the sudoers rule. The deploy pipeline's key is separate.
- **Refresh subprocess error** -- reproduce the SSH command as root with the same
  key and `known_hosts`, redirect its stdout to `/dev/null` so no token is shown,
  and read stderr.
- **MCP still uses old credentials** -- remove any overriding `K8S_*` values or
  legacy kubeconfig, confirm `refresh.json` matches the real Compose project,
  file, directory, and service, and confirm the container was recreated.
- **Cluster reinstall** -- recreate the reader RBAC, restore the helper and
  `api-server` config, and rerun the refresh service. The new CA and token are
  fetched together. Independently verify any changed SSH host key before updating
  `known_hosts`; a reinstall does not always rotate a retained CA, so inspect
  actual state rather than assuming.

## Reference values (one concrete example)

The steps above are identical for every cluster; the table below is one filled-in
set, for a K3s cluster, to make the variables concrete.

| Variable | Value |
| --- | --- |
| `CLUSTER` | `k3s` |
| `NAMESPACE` | `demo` |
| `READER_SA` | `inventory-reader` |
| `TOKEN_TTL` | `1h` |
| `API_URL` | `https://203.0.113.10:6443` |
| `KUBECONFIG_FILE` | `/opt/k8s-mcp/clusters/k3s.yaml` |
| `HELPER_PATH` | `/usr/local/sbin/kube-lab-mcp-credentials` |
| `HELPER_STATE_DIR` | `/etc/k8s-mcp` |
| `READER_SSH_USER` | `mcp-reader` |
| `SSH_HOST` | `203.0.113.10` |
| `SSH_PORT` | `22` |
| `MCP_HOME` | `/opt/k8s-mcp` |
| `COMPOSE_DIR` | `/opt/mcp` |
| `COMPOSE_FILE` | `docker-compose.yml` |
| `COMPOSE_SERVICE` | `mcp` |
| `COMPOSE_PROJECT` | `mcp` |

Note for the kube-lab node (`K3S-SETUP.md` block 8a in the `kube-lab` repo):
the helper there is provisioned with `HELPER_STATE_DIR=/etc/kube-lab` and reads
the API URL from `/etc/kube-lab/mcp-api-server` (the file name differs from the
generic `api-server` written by step 2). If you install step 2's helper verbatim
instead, either point it at the same location or keep `/etc/k8s-mcp` +
`api-server` consistently.

## References

- Kubernetes token requests: https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_token/
- ServiceAccount credentials: https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/
- Compose environment variables: https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/
- Compose service recreation: https://docs.docker.com/reference/cli/docker/compose/up/
