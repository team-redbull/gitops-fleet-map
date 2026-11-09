#!/bin/sh
# Installs (if missing) and starts a single-node k3s. kind is not usable here: its node image lives on Docker Hub, which is blocked.
set -eu
V=${K3S_VERSION:-v1.33.5+k3s1}; U=https://github.com/k3s-io/k3s/releases/download/$(echo "$V" | sed 's/+/%2B/')
if ! command -v k3s >/dev/null; then
  curl -fsSL -o /usr/local/bin/k3s "$U/k3s" && chmod +x /usr/local/bin/k3s && ln -sf k3s /usr/local/bin/kubectl
  mkdir -p /var/lib/rancher/k3s/agent/images && curl -fsSL -o /var/lib/rancher/k3s/agent/images/airgap.tar.zst "$U/k3s-airgap-images-amd64.tar.zst"
fi
k3s kubectl get nodes >/dev/null 2>&1 && exit 0
# NO_PROXY: kubectl logs / port-forward go API server -> kubelet on the node IP, which must not use the egress proxy
NP="${NO_PROXY:-},$(hostname -I | cut -d' ' -f1),e2e"
start() { NO_PROXY=$NP no_proxy=$NP nohup k3s server --disable traefik,metrics-server,local-storage,servicelb --write-kubeconfig-mode 644 --node-name e2e >/var/log/k3s.log 2>&1 & }
C=/var/lib/rancher/k3s/agent/etc/containerd
if [ ! -f $C/config-v3.toml.tmpl ]; then
  start; for i in $(seq 1 60); do [ -f $C/config.toml ] && break; sleep 2; done
  pkill -f "k3s server"; sleep 8
  # this sandbox lacks CAP_SYS_RESOURCE: runc cannot lower a pod sandbox's oom_score_adj ("nsexec: failed to update /proc/self/oom_score_adj")
  sed "s/^\[plugins.'io.containerd.cri.v1.runtime'\]\$/&\n  restrict_oom_score_adj = true/" $C/config.toml > $C/config-v3.toml.tmpl
fi
start
for i in $(seq 1 90); do k3s kubectl -n kube-system wait --for=condition=ready pod -l k8s-app=kube-dns --timeout=5s >/dev/null 2>&1 && exit 0; sleep 2; done
echo "k3s did not become ready" >&2; exit 1
