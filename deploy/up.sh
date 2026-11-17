#!/usr/bin/env bash
# Bring up the cluster on the laptop (wiki: Stack > Deployment on Kubernetes).
# Needs: docker, k3d, kubectl, mkcert. Secrets come from the environment, never from git (C-10):
#   HUB_TOKEN, APP_PASSWORD_HASH (from `uv run backend hash-password`), GRAFANA_ADMIN_PASSWORD
set -euo pipefail
cd "$(dirname "$0")/.."

: "${HUB_TOKEN:?set HUB_TOKEN}" "${APP_PASSWORD_HASH:?set APP_PASSWORD_HASH}" "${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}"

mkdir -p "$HOME/sweg9-data"/{server,backups,loki,hub-logs}
k3d cluster list sweg9 >/dev/null 2>&1 || k3d cluster create --config deploy/k3d.yaml

docker build -t sweg9/backend-server:dev .
k3d image import sweg9/backend-server:dev -c sweg9

kubectl apply -f deploy/k8s/namespaces.yaml
apply() { kubectl apply -f - ; }
kubectl create configmap server-settings -n sweg9 \
  --from-file=settings/server.yaml --from-file=settings/risk.yaml --dry-run=client -o yaml | apply
kubectl create secret generic app -n sweg9 \
  --from-literal=HUB_TOKEN="$HUB_TOKEN" --from-literal=APP_PASSWORD_HASH="$APP_PASSWORD_HASH" \
  --dry-run=client -o yaml | apply
kubectl create secret generic grafana -n logging \
  --from-literal=GRAFANA_ADMIN_PASSWORD="$GRAFANA_ADMIN_PASSWORD" --dry-run=client -o yaml | apply

certs="$HOME/sweg9-data/certs"
if [ ! -f "$certs/tls.crt" ]; then
  mkdir -p "$certs"
  mkcert -cert-file "$certs/tls.crt" -key-file "$certs/tls.key" sweg9.localhost grafana.sweg9.localhost
fi
for ns in sweg9 logging; do
  kubectl create secret tls sweg9-tls -n "$ns" --cert="$certs/tls.crt" --key="$certs/tls.key" \
    --dry-run=client -o yaml | apply
done

kubectl apply -k deploy/k8s
kubectl rollout restart deployment/server -n sweg9   # pick up a rebuilt image
kubectl rollout status deployment/server -n sweg9 --timeout=120s
echo "API: https://sweg9.localhost/api   Grafana: https://grafana.sweg9.localhost"
echo "Hub: HUB_TOKEN=... uv run backend hub   (writes ~/sweg9-data/hub-logs/hub.log)"
