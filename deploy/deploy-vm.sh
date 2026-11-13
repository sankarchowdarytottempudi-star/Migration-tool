#!/usr/bin/env bash
# Deploy Migration-tool into the VM's k3s. Run on the VM from the repo root:
#   cd ~/Migration-tool && bash deploy/deploy-vm.sh
# It builds the image, wires an SSH key the pod uses to drive the host toolchain, creates
# the admin-token secret, and applies the manifest (NodePort 30900).
set -euo pipefail
export PATH=$PATH:/usr/bin:/usr/local/bin
export KUBECONFIG=/root/.kube/config
cd "$(dirname "$0")/.."
log(){ echo "[mt-deploy] $*"; }

log "building image"
docker build -q -f deploy/Dockerfile -t migration-tool:vm . >/dev/null
docker save migration-tool:vm | k3s ctr images import - >/dev/null
log "image imported"

# SSH key the pod uses to reach the host (reuses one if already made)
KEY=~/.ssh/migration-tool
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "migration-tool-pod" >/dev/null
fi
grep -qF "$(cat "$KEY".pub)" ~/.ssh/authorized_keys 2>/dev/null || cat "$KEY".pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

NODE_IP=$(hostname -I | awk '{print $1}')
log "host target: root@${NODE_IP}"

ADMIN_TOKEN=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)

kubectl create namespace migration-tool --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n migration-tool create secret generic mt-ssh \
  --from-file=id_ed25519="$KEY" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n migration-tool create secret generic mt-admin \
  --from-literal=token="$ADMIN_TOKEN" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

sed "s#__SSH_TARGET__#root@${NODE_IP}#" deploy/k8s.yaml | kubectl apply -f -
kubectl -n migration-tool rollout status deployment/migration-tool --timeout=120s

echo
log "deployed.  UI:   http://${NODE_IP}:30900   (use the public IP from outside)"
log "admin token:  ${ADMIN_TOKEN}"
log "token also in secret migration-tool/mt-admin"
