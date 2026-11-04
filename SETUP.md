k3s.yaml or k8s.yaml must be generated on server which hosts kube 

Create roles in kube

```
NAMESPACE=demo
SERVICE_ACCOUNT=inventory-deployer

kubectl create rolebinding inventory-mcp-reader \
  --namespace "$NAMESPACE" \
  --clusterrole=view \
  --serviceaccount="$NAMESPACE:$SERVICE_ACCOUNT"
```

Then grant access to pod logs:
```
NAMESPACE=demo
SERVICE_ACCOUNT=inventory-deployer

kubectl create role inventory-pod-logs \
  --namespace "$NAMESPACE" \
  --verb=get \
  --resource=pods/log

kubectl create rolebinding inventory-pod-logs \
  --namespace "$NAMESPACE" \
  --role=inventory-pod-logs \
  --serviceaccount="$NAMESPACE:$SERVICE_ACCOUNT"

```

## Restricted kubeconfig for the MCP server

The MCP server does not need the admin k3s.yaml. Generate a kubeconfig for
the read-only service account from the section above, so the API server
itself rejects mutating calls (enforced by RBAC, not just code discipline).

```
NAMESPACE=demo
SERVICE_ACCOUNT=inventory-deployer

# 1. Make sure the service account exists (skip if it does)
kubectl get serviceaccount "$SERVICE_ACCOUNT" -n "$NAMESPACE" \
  || kubectl create serviceaccount "$SERVICE_ACCOUNT" -n "$NAMESPACE"

# 2. Issue a long-lived token
kubectl get secret "$SERVICE_ACCOUNT-token" -n "$NAMESPACE" \
  || kubectl create secret generic "$SERVICE_ACCOUNT-token" \
    --type=kubernetes.io/service-account-token \
    -n "$NAMESPACE"
TOKEN=$(kubectl -n "$NAMESPACE" get secret "$SERVICE_ACCOUNT-token" \
  -o jsonpath={.data.token} | base64 -d)

# 3. Cluster CA (already base64) from the admin kubeconfig
CA_DATA=$(kubectl config view --raw \
  -o jsonpath={.clusters[0].cluster.certificate-authority-data})

# 4. Restricted kubeconfig. server: 127.0.0.1 works because the container
#    runs on the same VPS; use the cluster IP from k3s.yaml if the
#    server runs on another machine (address must be in the k3s cert SAN)
cat > /opt/kubetraceback/mcp-kubeconfig.yaml <<EOF
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: https://127.0.0.1:6443
    certificate-authority-data: $CA_DATA
  name: k3s
contexts:
- context:
    cluster: k3s
    user: $SERVICE_ACCOUNT
  name: $SERVICE_ACCOUNT
current-context: $SERVICE_ACCOUNT
users:
- name: $SERVICE_ACCOUNT
  user:
    token: $TOKEN
EOF
chown 10001:10001 /opt/kubetraceback/mcp-kubeconfig.yaml
chmod 600 /opt/kubetraceback/mcp-kubeconfig.yaml
```

Then point the container at the new file (docker-compose.yml, volumes ->
source: /opt/kubetraceback/mcp-kubeconfig.yaml) and re-create it:

```
cd /opt/kubetraceback && docker compose up -d --force-recreate
```

Verify the restriction is in effect:

```
KC=/opt/kubetraceback/mcp-kubeconfig.yaml
kubectl --kubeconfig "$KC" get pods -n "$NAMESPACE"                    # allowed
kubectl --kubeconfig "$KC" auth can-i get pods/log -n "$NAMESPACE"     # yes
kubectl --kubeconfig "$KC" auth can-i create pods -n "$NAMESPACE"      # no
```

Notes:
- Deleting the token secret does not invalidate the token; to revoke, delete the service account and re-run the steps above.
- Keep the admin k3s.yaml on the VPS: CI (.github/workflows/ci.yml) verifies it exists before deploy. Only the container mount switches to the restricted file.
- If the MCP server runs on another machine, server: must match an address in the k3s certificate SAN.