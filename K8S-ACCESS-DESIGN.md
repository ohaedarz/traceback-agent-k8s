# Kubernetes access design (external MCP, multi-cluster)

This document describes how an MCP server that runs **outside** a Kubernetes
cluster obtains and keeps valid credentials to talk to that cluster. It records
the design and the reasoning behind each choice, so that reviewers can judge
whether the approach is sound. A concrete, parameterized runbook follows once
this is reviewed.

The MCP is a read-only observability client: it lists and watches pods, reads
logs, events, deployments, and services. It has no mutation capability, and the
design treats that as a property the cluster must enforce rather than a promise
the application makes. The server runs on an arbitrary host that can reach the
target API server over the network, and it must be able to point at any cluster
-- K3s, kubeadm, a distribution build, or a hosted control plane (EKS, GKE) --
without code changes.

## The overall shape

For each cluster the MCP is meant to observe, the cluster holds a **dedicated
read-only ServiceAccount** with a tightly scoped ClusterRole. A **credential
helper** on the cluster side mints a **short-lived bearer token** for that
ServiceAccount, and the resulting credential is written to the MCP host as a
**standard kubeconfig file** over a restricted channel that cannot do anything
but run that helper. A **timer on the MCP host** re-runs the helper ahead of
token expiry, verifies the new credential against the API server, and recreates
the MCP container so it loads the fresh credential. Pointing the MCP at a
different cluster is an operator action: run the same helper flow against the
new cluster and point the container at the new kubeconfig file.

The design rests on a single boundary. The MCP host is untrusted relative to
the cluster, so it should never hold a durable or privileged cluster credential.
Everything below -- credential shape, delivery, refresh -- follows from keeping
that boundary clean.

## Credential lifetime: short-lived, rotating tokens

The MCP authenticates with a ServiceAccount token minted with a finite
`--duration` (an hour by default), refreshed on a timer before it expires. This
is not an optimization; it is the only credential model that fits a modern
cluster plus an external, untrusted client.

Long-lived, non-expiring ServiceAccount tokens are effectively gone. The
`kubernetes.io/service-account-token` Secret type and the `kubectl create
secret --type=service-account-token` path that older guides rely on are the
legacy mechanism; current clusters either refuse to issue them or treat them as
deprecated, and a security review will flag them immediately. The supported
mechanism is the TokenRequest API (`kubectl create token <sa> --duration`),
which is inherently time-bounded.

The practical argument is blast radius. A token that is valid indefinitely is a
standing credential: once it leaks, it stays valid until a human rotates it, and
a token used by an external process is far more exposed than one used by a
pod-sidecar in the cluster network. A one-hour token means a leak is self-
limiting -- the credential dies within the hour even if the leak is never
detected -- and rotation is routine and automated rather than a rare, manual,
and therefore easily forgotten incident response.

## Delivery: a kubeconfig file, not environment variables

The credential is delivered as a standard kubeconfig file on the MCP host, one
file per cluster, and the MCP container reads it through `KUBECONFIG`.

A kubeconfig file is the native input for a Kubernetes client. The client
library already loads it through `config.load_kube_config()`; no new credential-
parsing code is introduced, and the file format carries exactly what the client
needs -- server URL, CA, and token -- in the shape every tool in the ecosystem
already understands. Choosing this format means the credential is auditable by
anyone with the usual tools, and the MCP can never be pointed at a cluster it was
not explicitly given a file for.

File-per-cluster is the natural layout for the one-cluster-at-a-time model. Each
cluster the MCP may watch corresponds to one file in a well-known directory,
selected by path. "Which cluster is this instance watching?" is answered by the
path itself, and rotating one cluster's credential does not touch the others.
An environment-variable scheme (`K8S_SERVER`, `K8S_CA_DATA`, `K8S_TOKEN`) would
not compose: a single container cannot carry five different token variables, so
you are back to one container per cluster anyway, but you have lost the
self-documenting path, the standard file format, and the existing client loader
in exchange for nothing the file cannot already express.

## Identity: a dedicated read-only ServiceAccount

The MCP authenticates as its own ServiceAccount with a ClusterRole that grants
`get`, `list`, and `watch` on pods, events, services, and deployments, plus
`get` on `pods/log`. No write verbs, no Secrets, no exec, no namespace listing
beyond what reading those resources requires.

This scopes the credential at the API server, which is the enforcement point that
actually matters. "The application never calls a write API" is a code-level
guarantee that breaks the moment the process is compromised or a bug issues an
unexpected request; a ClusterRole that simply does not grant the verb is enforced
by the control plane regardless of what the client attempts. A monitoring token
should be incapable of mutation by construction.

The monitoring identity is also kept strictly separate from any deployment
identity on the same cluster. A credential that deploys or changes workloads is
never the credential that observes them. This is a standard segregation rule: a
leaked monitoring token must be able to read state and nothing more, and a
leaked deployment token must not be usable by the monitoring path. Both accounts
are named per cluster -- nothing in the design depends on a particular literal
name -- the invariant is only that the monitoring SA is read-only and is not the
deployment SA.

The role is cluster-wide read rather than a single namespace. A diagnostics tool
that can only see one namespace cannot diagnose the cluster it is meant to
observe; cross-namespace read-only is the correct boundary for that purpose. A
specific cluster with a hard isolation requirement can tighten this to named
namespaces; the design does not preclude it.

## Mint path: a helper behind a restricted channel

Minting happens on the cluster side, in a root-owned helper. The helper reads the
cluster's own admin kubeconfig to obtain the CA, mints the short-lived token for
the configured ServiceAccount, and emits the credential material. It is a single
templated script parameterized by the ServiceAccount, namespace, token TTL, and
API URL, so the same helper serves every cluster with only different values.

The MCP host reaches the helper over a restricted channel: a dedicated SSH key
with a forced command that can run only that helper -- no shell, no other
commands, `restrict` applied. This is the only durable credential the MCP host
holds, and it is revocable by deleting one line from `authorized_keys`.

The important property is that **the admin kubeconfig never leaves the cluster**.
What crosses the boundary is the short-lived reader token and the CA, over a
channel that cannot do anything else. The MCP host is never handed cluster-admin,
a usable shell, or a token broader than the reader identity. This is the same
shape a hardened deploy helper uses -- a restricted credential confined to a
single script -- so the security model is one, consistent pattern rather than a
set of ad hoc arrangements per concern.

Copying the admin kubeconfig to the MCP host is not an alternative the design
accepts. It would hand full cluster admin to a monitoring container that runs
arbitrary (if untrusted) network-facing code; that is the direct opposite of the
separation above and would make a single compromised process equivalent to full
cluster compromise.

## Refresh: a timer renews ahead of expiry

A timer on the MCP host re-runs the mint flow on an interval shorter than the
token TTL (a few minutes against an hour of lifetime). It reads the current
token's expiry, mints a fresh one when it is within threshold, verifies the new
credential by making a real API call with it, writes the new kubeconfig file
atomically, and recreates the MCP container.

The container recreation is not incidental. The client is constructed once at
process start and cached for the lifetime of the process; a new file on disk is
invisible to a running process that has already loaded its config. So the refresh
mechanism must replace the process, not just the file, or the MCP would silently
run on the old token until it expired and then fail.

Verification before replacement is deliberate. If the freshly minted token cannot
authenticate -- a network blip, a rotated CA, a misconfiguration -- the timer
keeps the old file so the running MCP continues on its still-valid token rather
than tearing down a working monitor to install a broken credential. A failed
renewal degrades to the old token's remaining lifetime instead of causing an
outage.

Renewal is a host concern, deliberately decoupled from any deployment pipeline.
The MCP keeps its credentials fresh whether or not a build system runs; tying
token refresh to a deploy step would couple the lifetime of a running monitor to
an unrelated schedule.

## Multi-cluster: one instance, one cluster, re-pointed by the runbook

The MCP is a single instance pointing at one cluster at a time. The client builds
one API client from one `KUBECONFIG`; it is not a multi-cluster multiplexer, and
the requirement is explicitly one cluster at a time with re-pointing as an
operator action.

Because the credential is a file selected by path, the helper is a template, and
the identity is a per-cluster ServiceAccount rather than a hardcoded name,
monitoring a different cluster requires no code change and no new container
definition. The operator runs the same helper flow with that cluster's values --
its own reader ServiceAccount, namespace, and API URL -- to produce that
cluster's kubeconfig file, then points the container at it. The runbook is
parameterized by cluster; the set of blocks is identical for every cluster.

A single MCP process that multiplexed many clusters would need to be rewritten
into a per-request, cluster-scoped client manager -- a substantially larger
change to the client layer, justified only by a requirement that does not exist.
The file-per-cluster layout already delivers the multi-cluster capability the
design is asked for, with each cluster independently rotatable and independently
revocable.

