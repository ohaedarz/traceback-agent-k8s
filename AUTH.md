# Keycloak Authentication for kube-traceback

How to enable and verify Keycloak (OIDC) auth for the HTTP transport: what
to configure on the Keycloak side (client, scopes, roles), where each
environment variable lives, and how MCP clients connect.

> **Scope:** auth is **opt-in**. When `KEYCLOAK_ISSUER_URL` is unset (or when
> using the `stdio` transport), the server is unauthenticated and every
> namespace is allowed. The `stdio` transport never authenticates — it is a
> local transport. Auth only applies to the `streamable-http` transport.

**Reading order for a first setup:** 1 → 2 → 3 → 4 → 5 → 6.

---

## 1. How it works

- When `KEYCLOAK_ISSUER_URL` is set, the server starts with Keycloak OIDC
  auth: MCP clients obtain a bearer JWT (authorization code + PKCE flow) and
  present it on every request.
- `src/kubetraceback/auth.py::KeycloakTokenVerifier` validates each token:
  - `alg` must be `RS256`;
  - signature checked against the realm JWKS
    (`$ISSUER/protocol/openid-connect/certs`), cached per `kid`, refetched
    once on an unknown `kid` (key rotation);
  - `iss` must equal `KEYCLOAK_ISSUER_URL` (trailing slash stripped);
  - `aud` must contain the MCP client id (`KEYCLOAK_CLIENT_ID`);
  - `exp` must be in the future (30 s clock skew allowed).
- **Namespace RBAC** is role-based and checked inside every tool
  (`auth.check_namespace(ns)`):
  - A Keycloak **client role** named `ns:<namespace>` (on the MCP client)
    grants read access to that namespace.
  - **Realm roles** with the same `ns:<namespace>` naming work too.
  - `ns:*` grants every namespace.
  - No matching role = `NamespaceNotAllowed` error returned by the tool.
  - Multiple `ns:` roles are unioned; the prefix is configurable via
    `KEYCLOAK_NS_ROLE_PREFIX` (default `ns:`).

Example: user `jdoe` with client roles `ns:demo` + `ns:prod` can call
`find_pods_by_name(..., namespace="demo")` and `... namespace="prod"`, but a
call for `kube-system` fails with:

```
namespace 'kube-system' is not accessible to user 'jdoe' (allowed namespaces: demo, prod)
```

---

## 2. Environment variables — where each one lives

All variables are read by the **server process** at request/startup time.
Where you put them depends on how the server is deployed:

| Variable | Where it is defined | Notes |
|---|---|---|
| `KEYCLOAK_ISSUER_URL` | **Docker Compose `environment:`** (VPS) — or shell env for local dev | Required to enable auth. Value = realm issuer, e.g. `https://<your-auth-domain>/realms/<realm>`. Must be exactly what Keycloak reports as `issuer` (check `/.well-known/openid-configuration` in Keycloak). |
| `KEYCLOAK_CLIENT_ID` | Compose `environment:` (optional) | Default `mcp-kubetraceback`. Must match the Keycloak client name you create in §3. |
| `KEYCLOAK_REQUIRED_SCOPE` | Compose `environment:` (optional) | Default `k8s:readonly`. Must match the client scope name from §3.2. |
| `KEYCLOAK_NS_ROLE_PREFIX` | Compose `environment:` (optional) | Default `ns:`. |
| `MCP_RESOURCE_URL` | Compose `environment:` (optional) | The **public** URL of the MCP endpoint (what clients connect to). Default is derived from `TRACEBACK_MCP_HOST`/`TRACEBACK_MCP_PORT` (useful only for loopback dev). Set it when serving through a reverse proxy/domain. |
| `TRACEBACK_MCP_TRANSPORT` | Compose `environment:` | `http` for the network transport (auth only matters here). `stdio` for local clients. |
| `TRACEBACK_MCP_HOST` / `TRACEBACK_MCP_PORT` | Compose `environment:` | Bind address/port **inside the container's publish mapping** (default `0.0.0.0:8765`). |
| `KUBECONFIG` | Compose `environment:` + a **bind mount** of the kubeconfig file | Path *inside the container*. The host file is mounted read-only (see `docker-compose.yml`). |

### Where do they go — not where?

- **Docker Compose on the VPS (primary deployment):** the `environment:`
  block of the `mcp` service in `docker-compose.yml` (repo root). This file
  is part of the repo and is deployed by the CI workflow — **edit it in git,
  push to `main`, and CI rebuilds + redeploys.** Do not hand-edit the file
  on the VPS; the next deploy overwrites it.
- **GitHub Actions:** do **not** put these in workflow secrets. The
  workflow (`.github/workflows/ci.yml`) only needs SSH credentials to
  deploy; the Keycloak config ships inside `docker-compose.yml` itself.
  (If you ever move to a deployment that reads secrets at runtime — e.g.
  k8s `Secret` objects — then the values go there instead, and
  `docker-compose.yml` references them via `${VAR}` interpolation with a
  `.env` file on the host.)
- **Local development (laptop, stdio):** plain shell exports before running
  `python -m kubetraceback` — or nothing at all, since stdio is
  unauthenticated and auth is irrelevant for local testing.
- **Secrets:** none of the variables above are secrets. The Keycloak *client
  secret* is never needed by the server (it only verifies public-key
  signatures); it is needed by the *MCP client* side if it uses the
  confidential flow.

### Reference: the compose block for the VPS

This is the shape the `environment:` block should have in
`docker-compose.yml` (values are placeholders — fill in yours in the repo):

```yaml
services:
  mcp:
    image: kube-traceback:ci
    pull_policy: never
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8765"
    environment:
      TRACEBACK_MCP_TRANSPORT: "http"
      TRACEBACK_MCP_HOST: "0.0.0.0"
      TRACEBACK_MCP_PORT: "8765"
      TRACEBACK_DEFAULT_NAMESPACE: "demo"
      TRACEBACK_LOG_TAIL_LINES: "40"
      KUBECONFIG: "/home/agent/.kube/config"
      # --- Keycloak auth (uncomment to enable) ---
      KEYCLOAK_ISSUER_URL: "https://<your-auth-domain>/realms/<realm>"
      # KEYCLOAK_CLIENT_ID: "mcp-kubetraceback"
      # KEYCLOAK_REQUIRED_SCOPE: "k8s:readonly"
      # MCP_RESOURCE_URL: "https://<public-mcp-domain>/mcp"
    volumes:
      - type: bind
        source: /opt/k8s-mcp/clusters/k3s.yaml
        target: /home/agent/.kube/config
        read_only: true
        bind:
          create_host_path: false
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import urllib.request;
          urllib.request.urlopen(
          'http://127.0.0.1:8765/health',
          timeout=3).close()
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

> **Note:** the JWKS endpoint
> (`$KEYCLOAK_ISSUER_URL/protocol/openid-connect/certs`) must be reachable
> from the server container (outbound HTTPS). If Keycloak is on the same
> host behind a reverse proxy, that works as long as the proxy serves a
> trusted TLS cert.

---

## 3. Keycloak setup (admin console)

Done **once, in the browser** (or via `kc.sh` — see 3.5). Assume realm
**`<realm>`** — use whatever realm your `KEYCLOAK_ISSUER_URL` points at
(`master` or a dedicated one).

### 3.1 Create the MCP client

1. *Clients → Create client*
   - **Client ID:** `mcp-kubetraceback` (must match `KEYCLOAK_CLIENT_ID`)
   - **Client type:** OpenID Connect
   - **Access type:** `confidential` (the authorization code flow needs a
     client secret)
2. Open the client → **Login** tab:
   - **Valid redirect URIs:** enter the two lines below (see explanation
     after this list):
     ```
     http://localhost:6274/callback
     http://localhost:*
     ```
   - **Web origins:** `+`
3. **Authentication** tab:
   - **Authorization code flow:** ON
   - **Proof key for code exchange (PKCE):** ON (or "Required")
4. Copy the **client secret** (Credentials tab) — the MCP client needs it
   for the confidential flow. The server does not.

#### What "Valid redirect URIs" means and what to enter

The redirect URI is **the URL in the user's browser (on the user's laptop)
that receives the OAuth response after login** — not a URL on the VPS, Keycloak,
or the MCP server. The flow:

```
1. Browser on laptop:  MCP client starts the login
2. Browser:            redirected to Keycloak ...&redirect_uri=http://localhost:6274/callback&...
3. User:               logs in to Keycloak in that tab
4. Browser:            Keycloak redirects to http://localhost:6274/callback?code=...
5. Laptop:             the MCP client app is LISTENING on that local port,
                       catches the code, exchanges it for a token
```

Keycloak **refuses step 4** if the `redirect_uri` is not on the list — that is
the whole point of the setting. Two client categories exist:

- **Local/desktop clients** (MCP Inspector, Cursor, Cline, Claude Desktop):
  the callback lands on `http://localhost:<port>` on the user's machine.
  The port is chosen by the client (sometimes a random one per run).
  The two lines above cover all of them: the exact Inspector URL plus the
  `http://localhost:*` wildcard (Keycloak allows one wildcard in the host
  position), which whitelists any local port.
- **Hosted clients** (ChatGPT, Claude.ai, other server-side products):
  they manage their own OAuth flow (dynamic client registration or
  pre-registered credentials) — **no localhost redirect URIs are involved**.
  Configure them in section 5.2.

If a local client ever shows a Keycloak error like
`The redirect URI is not authorized for this client ... request redirect_uri
is: http://localhost:52437/callback`, copy that exact URL and add it — but
with the `http://localhost:*` line in place you will rarely need to.

> The wildcard only whitelists *local* redirects. The user must still log in
> to Keycloak, and access is scoped by their `ns:*` roles, so a wildcard
> here is acceptable for a VPS used by your own clients.

### 3.2 Create the scope

1. Still on the `mcp-kubetraceback` client → **Client scopes** (realm level:
   *Realm settings → Client scopes*):
   - **Create client scope** → **Name:** `k8s:readonly`
2. Back on the client → **Client scopes → Mapped client scopes → Add
   associated client scope** → pick `k8s:readonly`, set **Default: ON**
   (so it is always included in issued tokens).
   - (If you use a different `KEYCLOAK_REQUIRED_SCOPE`, name it accordingly.)

### 3.3 Create users + namespace roles

1. *Users → Add user*, e.g. `jdoe` (needs a login: email/password, email
   verified if enforced).
2. Grant namespace access — one or both:
   - **Client roles** (recommended, scoped to the MCP client):
     `jdoe` → *Role mapping* → *Client roles* → `mcp-kubetraceback` →
     *Add client role* → create `ns:demo`, `ns:prod`, … as needed.
   - **Realm roles:** *Realm settings → Roles* → create `ns:demo`, etc.,
     then assign to the user under *Role mapping → Realm roles*.
3. **Wildcard:** role `ns:*` grants every namespace.
4. Repeat per user: each person gets exactly the `ns:*` roles they should
   read.

### 3.4 Summary of what a working user looks like

```
User: jdoe
  client roles on mcp-kubetraceback: ns:demo, ns:prod
  (optional) realm roles:            ns:staging
Token scopes:   k8s:readonly   (default client scope, always included)
Token aud:      mcp-kubetraceback
Token iss:      https://<your-auth-domain>/realms/<realm>
```

### 3.5 Same steps via `kc.sh` (optional, scriptable)

From a host that can reach Keycloak (admin user from
`KC_BOOTSTRAP_ADMIN_USERNAME`/`PASSWORD`):

```bash
export KC_URL="https://<your-auth-domain>"
export KC_REALM="<realm>"

# login (password grant for the admin user)
./kc.sh config token --server "$KC_URL" --realm master \
  --user "$KC_ADMIN_USER" --password "$KC_ADMIN_PASSWORD"

# 1. client (confidential, code flow + PKCE)
./kc.sh create clients -r "$KC_REALM" \
  -n mcp-kubetraceback \
  --clientAuthMethod client-secret \
  --redirectUris "http://localhost:6274/callback,http://localhost:*/*" \
  --webOrigins "+" \
  --attributes "oauth2.device.authorization.grant.enabled=false"

# 2. client scope, default-on for the client
./kc.sh create client-scopes -r "$KC_REALM" -n k8s:readonly
./kc.sh update clients -r "$KC_REALM" -i mcp-kubetraceback \
  --defaultClientScopes "web-origins,profile,email,roles,offline_access,k8s:readonly"

# 3. namespace client roles
./kc.sh create client-scopes -r "$KC_REALM" -n ns:demo    # per-namespace grant, if preferred as a scope
# (role-based grants are usually created in the console; kc.sh role APIs:
#  kc.sh create client-roles -r "$KC_REALM" -c mcp-kubetraceback -n "ns:demo"
#  kc.sh assign client-roles -r "$KC_REALM" -u jdoe -c mcp-kubetraceback -n "ns:demo")

# 4. user + assign role
./kc.sh create users -r "$KC_REALM" -u jdoe -e jdoe@example.com
./kc.sh assign client-roles -r "$KC_REALM" -u jdoe -c mcp-kubetraceback -n "ns:demo"
```

(Verify exact flag names against your Keycloak version — `kc.sh --help
<command>`; the console steps in 3.1–3.3 are the source of truth.)

---

## 4. Deploy the server with auth

The server runs in **Docker** on the VPS (see `Dockerfile` +
`docker-compose.yml`). There is no "run python on the host" step in the
production path.

### 4.1 Enable auth in `docker-compose.yml` (in git)

1. In the repo, edit `docker-compose.yml` — add the Keycloak variables to
   the `mcp` service's `environment:` block (see the reference block in §2).
2. Commit and push to `main`. The CI workflow
   (`.github/workflows/ci.yml`) builds the image and deploys it to the VPS
   (`/opt/kubetraceback`) automatically — it re-runs `docker compose up -d
   --force-recreate mcp`, which picks up the new environment.
3. **No host-level env files are needed.** Everything the container requires
   is in `docker-compose.yml`. (The one host file is the kubeconfig at
   `/opt/k8s-mcp/clusters/k3s.yaml`, bind-mounted read-only — already in
   place.)

### 4.2 Verify the running container

On the VPS:

```bash
cd /opt/kubetraceback
docker compose ps                 # mcp: healthy
docker compose logs --tail=50 mcp # no auth errors
docker compose exec mcp env | grep KEYCLOAK   # confirm vars are in the container
```

From anywhere (through the reverse proxy):

```bash
# discovery endpoint (no auth)
curl -s https://<public-mcp-domain>/.well-known/oauth-authorization-server | jq
# health (no auth)
curl -s https://<public-mcp-domain>/health
# MCP request WITHOUT a token -> 401 + WWW-Authenticate
curl -si -X POST https://<public-mcp-domain>/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
```

If `401` appears on the unauthenticated request, auth is live.

### 4.3 Local development (laptop, optional)

For quick experiments without Docker:

```bash
uv sync --extra dev
export KUBECONFIG=$HOME/.kube/config
# stdio transport: no auth needed at all
.venv/Scripts/python -m kubetraceback        # Windows
# or, to test the HTTP transport + auth locally:
export TRACEBACK_MCP_TRANSPORT=http
export KEYCLOAK_ISSUER_URL="https://<your-auth-domain>/realms/<realm>"
.venv/Scripts/python -m kubetraceback
```

---

## 5. Connect an MCP client

### 5.1 MCP Inspector (quick test)

```bash
npx @modelcontextprotocol/inspector
```

1. Connection type: **Streamable HTTP**
2. Server URL: the public MCP endpoint (e.g. `https://<public-mcp-domain>/mcp`)
3. It fetches `/.well-known/oauth-authorization-server`, opens the Keycloak
   login page in your browser; you log in as a user with the right `ns:`
   roles.
4. The redirect to `http://localhost:6274/callback` must be in the
   Keycloak client's **Valid redirect URIs** (§3.1).
5. After login: the tool list appears. Calls in allowed namespaces work;
   others return the `NamespaceNotAllowed` message.

### 5.2 Local / desktop clients (Cursor, Claude Desktop, Cline, …)

Standard MCP OAuth (RFC 9728 / 8414 discovery):

```json
{
  "mcpServers": {
    "kube-traceback": {
      "url": "https://<public-mcp-domain>/mcp"
    }
  }
}
```

The client opens the browser on the user's laptop, performs the
authorization-code + PKCE flow against Keycloak, and receives the redirect
on its local callback port. The Keycloak whitelist from §3.1
(`http://localhost:6274/callback` + `http://localhost:*`) already covers
every local client — there is nothing per-client to configure.

Some clients also accept a pre-fetched static bearer token:

```json
{
  "mcpServers": {
    "kube-traceback": {
      "url": "https://<public-mcp-domain>/mcp",
      "headers": { "Authorization": "***" }
    }
  }
}
```

A static token works for quick tests (Keycloak admin → *Token* generator,
or the token endpoint directly), but interactive code+PKCE is preferred.

### 5.3 Hosted clients (ChatGPT, Claude.ai, other server-side products)

Hosted clients run **server-side** — no browser on your laptop, no localhost
redirect. They connect to the MCP endpoint from their own infrastructure and
use either:

- **dynamic client registration** (the client registers itself with Keycloak
  at runtime — enable it in the realm: *Realm settings → Client
  Registration* → dynamic client registration), or
- **pre-registered credentials**: you paste the `mcp-kubetraceback` client
  ID + secret (Credentials tab) into the hosted product's "connect MCP
  server" settings.

Setup:

1. In the hosted product's MCP settings, add a remote server at
   `https://<public-mcp-domain>/mcp`.
2. Follow whatever it asks for:
   - asks only for the URL → discovery + dynamic client registration;
     make sure that is enabled in the realm;
   - asks for client ID / secret → use the `mcp-kubetraceback` ones;
   - asks for an "authorization server" URL → give it the realm issuer
     (`https://<your-auth-domain>/realms/<realm>`).
3. The product must be able to reach both the MCP endpoint **and** the
   Keycloak issuer (JWKS / token endpoints) over the public internet with a
   trusted TLS certificate.

Notes:

- Access control is identical for every client type: the server checks
  `iss` / `aud` / `exp` on the token and maps the **user's** `ns:*` roles to
  namespaces. Which app obtained the token does not matter.
- Hosted products change their OAuth support over time; when you connect,
  the product's UI tells you exactly what it needs.

### 5.4 Manual checks (curl)



```bash
# JWKS the server verifies against (must be reachable from the server)
curl -s https://<your-auth-domain>/realms/<realm>/protocol/openid-connect/certs | jq '.keys | length'

# unauthenticated MCP call -> 401
curl -si -X POST https://<public-mcp-domain>/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5

# with a valid token -> 200 + tool list
curl -s -X POST https://<public-mcp-domain>/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 6. Verification checklist

- [ ] `curl $ISSUER/protocol/openid-connect/certs` from the **VPS** returns keys.
- [ ] `docker compose exec mcp env | grep KEYCLOAK` shows the expected vars.
- [ ] Unauthenticated `POST /mcp` returns `401` (not `403`, not 200).
- [ ] Login via Keycloak UI works; token's `iss` / `aud` / `scope` match the env vars.
- [ ] Tool call in an allowed namespace returns data.
- [ ] Tool call in a non-allowed namespace returns the
      `namespace '...' is not accessible to user '...'` message.
- [ ] After rotating the realm signing key, the first request with a new
      `kid` still succeeds (JWKS refetch); subsequent requests use the cache.
- [ ] `pytest` still passes (auth tests are offline/mocked).

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401` on every request, token looks right | `iss` mismatch (trailing slash, http vs https) or `aud` != `KEYCLOAK_CLIENT_ID`. |
| `401` after key rotation | JWKS endpoint unreachable from the container; check outbound HTTPS / proxy cert trust. |
| `403` / scope error | Token missing `KEYCLOAK_REQUIRED_SCOPE`; re-login after client-scope changes. |
| Namespace denied but role exists | Role is on the wrong client, wrong prefix, or user role mapping not saved; check *Role mapping* in the user screen. |
| Redirect fails in the browser | MCP client callback URI missing from **Valid redirect URIs**. |
| stdio works, http rejects everything | Auth is only configured for the http transport; ensure `TRACEBACK_MCP_TRANSPORT=http` and the vars are set *in the same process* (i.e. in the compose `environment:` block). |
| Changes on the VPS vanish after a deploy | You edited `/opt/kubetraceback/docker-compose.yml` by hand. Edit it **in the repo** instead — CI overwrites the host copy on every push to `main`. |

Token verification failures are logged at `DEBUG` level
(`logging.getLogger("kubetraceback.auth")`). To see exactly which check
(`alg` / signature / `iss` / `exp` / `aud`) rejected a token, add
`PYTHONUNBUFFERED=1` and enable debug logging in the container
(e.g. `docker compose run --rm mcp python -c "import logging; ..."` or
temporarily bump the logger level in code).
