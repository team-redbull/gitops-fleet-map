#!/bin/sh
# Brings the whole e2e environment up: fake GitLab + seed, k3s, image (build-ubuntu.sh if gitops-fleet-map:1.3.0 is missing) + load, Deployment, port-forward on :18080.
set -eu
E=$(cd "$(dirname "$0")" && pwd); S=$E/.state; mkdir -p "$S"
sh "$E/gitlab.sh" start
python3 "$E/seed.py" http://127.0.0.1:8929 "$(cat "$S/root.token")"
sh "$E/k3s.sh"
docker image inspect gitops-fleet-map:1.3.0 >/dev/null 2>&1 || sh "$E/../build-ubuntu.sh"
docker image inspect gitops-fleet-map:1.3.0 -f 'image size: {{.Size}} bytes'
docker save gitops-fleet-map:1.3.0 | k3s ctr -n k8s.io images import - >/dev/null
# pods reach the host (where GitLab runs) on the CNI bridge address
HOSTIP=${HOSTIP:-10.42.0.1}
k3s kubectl create secret generic gitops-fleet-map-token --dry-run=client -o yaml --from-file=token="$S/read.token" | k3s kubectl apply -f -
sed "s/__HOSTIP__/$HOSTIP/g" "$E/k8s.yaml" | k3s kubectl apply -f -
k3s kubectl rollout restart deploy/gitops-fleet-map >/dev/null
k3s kubectl rollout status deploy/gitops-fleet-map --timeout=120s
# rollout status returns while the old pod is still terminating, and a port-forward started then dies with it
for i in $(seq 1 60); do [ "$(k3s kubectl get pods -l app=gitops-fleet-map --no-headers | wc -l)" = 1 ] && break; sleep 1; done
pkill -f "svc/gitops-fleet-map 18080" || true
# supervised: the forward dies whenever the pod is replaced (the outage test does that on purpose)
nohup sh -c 'while true; do k3s kubectl port-forward svc/gitops-fleet-map 18080:8080; sleep 1; done' >"$S/pf.log" 2>&1 &
for i in $(seq 1 30); do curl -sf --noproxy '*' localhost:18080/inventory.json >/dev/null && echo "up: http://localhost:18080/" && exit 0; sleep 2; done
echo "page did not appear" >&2; exit 1
