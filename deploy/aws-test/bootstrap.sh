#!/bin/bash
# Runs on the test instance (setup.sh copies it there): Nitro Enclaves, the enclave image and its EIF, k3s and
# the server pod. Everything is built here, so nothing is pushed to a registry. Prints the enclave's PCR0 and the
# HUB_TOKEN at the end (a throwaway token for this test only).
set -euxo pipefail
AI_ENGINE_REV=28be8fb6d74137ea09447573d60973996eb38313
ENCLAVE_MIB=8192
W=/opt/sweg9
sudo mkdir -p $W && sudo chown "$USER" $W && cd $W

# 1. Docker, Nitro Enclaves (2 vCPUs and 8 GiB for the enclave), git
sudo dnf install -y docker git aws-nitro-enclaves-cli aws-nitro-enclaves-cli-devel
sudo usermod -aG docker,ne "$USER"
sudo sed -i "s/^cpu_count:.*/cpu_count: 2/; s/^memory_mib:.*/memory_mib: $ENCLAVE_MIB/" /etc/nitro_enclaves/allocator.yaml
sudo systemctl enable --now docker nitro-enclaves-allocator.service
D="sudo docker"

# 2. The code
if [ -d backend-server ]; then git -C backend-server pull -q --ff-only; else git clone --depth 1 https://github.com/sweg9/backend-server; fi
[ -d ai-engine ] || { git clone https://github.com/sweg9/ai-engine && git -C ai-engine checkout -q $AI_ENGINE_REV; }
[ -d sdk-c ] || git clone --depth 1 https://github.com/aws/aws-nitro-enclaves-sdk-c sdk-c

# 3. kmstool_enclave_cli and libnsm.so, AWS's own build (opens the data key with the enclave's attestation)
if [ ! -f sdk-c/bin/kmstool-enclave-cli/kmstool_enclave_cli ]; then
  (cd sdk-c/bin/kmstool-enclave-cli && sudo ./build.sh)
fi

# 4. The enclave image and its EIF. Test models: the people detector (COCO yolo11s, FR-10); the control
#    detector and the effect check are not in a repo yet, so the test settings leave them out (they answer safely).
mkdir -p ctx/engine/linker ctx/engine/models
rm -rf ctx/backend-server && cp -r backend-server ctx/ && rm -rf ctx/backend-server/.git
cp ai-engine/linker/prior.json ctx/engine/linker/
[ -f ctx/engine/models/yolo11s.pt ] || curl -fsSL -o ctx/engine/models/yolo11s.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s.pt
cp sdk-c/bin/kmstool-enclave-cli/kmstool_enclave_cli sdk-c/bin/kmstool-enclave-cli/libnsm.so ctx/
$D build -f backend-server/deploy/enclave/Dockerfile --build-arg AI_ENGINE_REV=$AI_ENGINE_REV -t sweg9/ml-engine:test ctx
# linuxkit needs about 5 GB of RAM for this image, and the allocator holds 8 GiB for the enclave: free it meanwhile.
sudo nitro-cli terminate-enclave --all || true
sudo systemctl stop nitro-enclaves-allocator.service
sudo find /tmp -maxdepth 1 -regex '/tmp/[0-9]+' -delete   # exports left by a failed build
sudo nitro-cli build-enclave --docker-uri sweg9/ml-engine:test --output-file $W/ml-engine.eif | tee $W/eif.json
sudo systemctl start nitro-enclaves-allocator.service
python3 -c "import json;print(json.load(open('$W/eif.json'))['Measurements']['PCR0'])" > $W/pcr0

# 5. The enclave and the vsock proxy it reaches KMS through. Not in debug mode: there the PCRs are zeros and
#    KMS refuses (C-05).
sudo tee /etc/systemd/system/sweg9-vsock-proxy.service >/dev/null <<EOF
[Unit]
Description=vsock proxy from the enclave to KMS
After=network-online.target
[Service]
ExecStart=/usr/bin/vsock-proxy 8000 kms.ap-northeast-2.amazonaws.com 443
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/sweg9-enclave.service >/dev/null <<EOF
[Unit]
Description=sweg9 ML Engine in a Nitro Enclave
After=nitro-enclaves-allocator.service sweg9-vsock-proxy.service
Requires=nitro-enclaves-allocator.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/nitro-cli run-enclave --eif-path $W/ml-engine.eif --cpu-count 2 --memory $ENCLAVE_MIB --enclave-cid 16
ExecStop=/usr/bin/nitro-cli terminate-enclave --all
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now sweg9-vsock-proxy
sudo systemctl enable sweg9-enclave && sudo systemctl restart sweg9-enclave   # a rerun starts the new EIF
sleep 20
sudo nitro-cli describe-enclaves | grep -q '"State": "RUNNING"' || { echo "the enclave stopped right after starting" >&2; exit 1; }

# 6. k3s and the server pod (the image is imported, not pulled)
command -v k3s >/dev/null || curl -sfL https://get.k3s.io | sudo sh -s - --write-kubeconfig-mode 644
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
$D build -t sweg9/backend-server:test backend-server
$D save sweg9/backend-server:test | sudo k3s ctr images import -
for _ in $(seq 60); do kubectl get crd ingressroutes.traefik.io >/dev/null 2>&1 && break; sleep 5; done

kubectl apply -f backend-server/deploy/k8s/namespaces.yaml
TOKEN=$(openssl rand -hex 24)
kubectl -n sweg9 create secret generic app --from-literal=HUB_TOKEN="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
S=backend-server/settings
kubectl -n sweg9 create configmap server-settings --from-file=$S/server.yaml --dry-run=client -o yaml | kubectl apply -f -
sed -e 's#^\(  detector_weights:\).*#\1 null#' -e 's#^\(  effect_weights:\).*#\1 null#' ai-engine/settings.yaml > $W/ai-engine.yaml
kubectl -n sweg9 create configmap enclave-settings --from-file=$S/risk.yaml --from-file=$S/enclave.yaml \
  --from-file=ai-engine.yaml=$W/ai-engine.yaml --dry-run=client -o yaml | kubectl apply -f -
kubectl kustomize --load-restrictor LoadRestrictionsNone backend-server/deploy/aws-test/k8s | kubectl apply -f -   # it reuses ../../k8s
kubectl -n sweg9 rollout status deploy/server --timeout=180s

set +x
echo "PCR0=$(cat $W/pcr0)"
echo "HUB_TOKEN=$TOKEN"
nitro-cli describe-enclaves
