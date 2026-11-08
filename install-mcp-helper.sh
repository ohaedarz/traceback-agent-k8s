#!/usr/bin/env bash
set -euo pipefail

: "${HELPER_STATE_DIR:?Pass HELPER_STATE_DIR to this script}"
: "${HELPER_PATH:?Pass HELPER_PATH to this script}"
: "${API_URL:?Pass API_URL to this script}"
: "${READER_SA:?Pass READER_SA to this script}"
: "${NAMESPACE:?Pass NAMESPACE to this script}"
: "${TOKEN_TTL:?Pass TOKEN_TTL to this script}"

if (( EUID != 0 )); then
    echo 'Run this installer as root.' >&2
    exit 1
fi

install -d -o root -g root -m 755 -- "$HELPER_STATE_DIR"
printf '%s\n' "$API_URL" > "$HELPER_STATE_DIR/api-server"
chown root:root -- "$HELPER_STATE_DIR/api-server"
chmod 644 -- "$HELPER_STATE_DIR/api-server"

helper_tmp=$(mktemp -- "${HELPER_PATH}.XXXXXX")
trap 'rm -f -- "$helper_tmp"' EXIT

{
    printf '%s\n' '#!/usr/bin/python3 -I'
    /usr/bin/python3 -I - \
        "$HELPER_STATE_DIR" "$READER_SA" "$NAMESPACE" "$TOKEN_TTL" <<'PY'
import sys

for name, value in zip(("STATE_DIR", "SA", "NS", "DURATION"), sys.argv[1:]):
    print(f"{name} = {value!r}")
PY
    cat <<'PY'

import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit


def kube(*args):
    return subprocess.check_output([
        "/usr/local/bin/k3s", "kubectl",
        "--kubeconfig=/etc/rancher/k3s/k3s.yaml",
        *args,
    ], text=True, timeout=30)


api_server_file = Path(STATE_DIR) / "api-server"
server = api_server_file.read_text().strip()
url = urlsplit(server)
if url.scheme != "https" or not url.hostname or url.username or url.password:
    raise SystemExit(f"Configure a valid HTTPS API URL in {api_server_file}")

config = json.loads(kube(
    "config", "view", "--raw", "--flatten", "--minify", "-o", "json"
))
ca = config["clusters"][0]["cluster"]["certificate-authority-data"]
token = kube("-n", NS, "create", "token", SA, f"--duration={DURATION}").strip()

part = token.split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
expiry = int(claims["exp"])
if expiry - time.time() < 1200:
    raise SystemExit("Issued token lifetime is under 20 minutes")

print(json.dumps({
    "K8S_SERVER": server,
    "K8S_CA_DATA": ca,
    "K8S_TOKEN": token,
    "K8S_SERVICE_ACCOUNT": SA,
    "expires_at": expiry,
}))
PY
} > "$helper_tmp"

# Test credential issuance without printing the token.
/usr/bin/python3 -I "$helper_tmp" >/dev/null
chown root:root -- "$helper_tmp"
chmod 755 -- "$helper_tmp"
mv -fT -- "$helper_tmp" "$HELPER_PATH"
printf 'Helper OK: %s\n' "$HELPER_PATH"
