# Deployment on AWS

How to set up the server for the design of 2026-11-20 (wiki: [Stack > Deployment on AWS](https://sweg9.github.io/docs/overview/stack#deployment-on-aws)). Not run yet. The code still follows the 2026-11-17 split (see the repository README), so these steps describe the target.

`nova.example.com` in the manifests stands for the server's domain, which is still open.

## 1. The instance

- An EC2 instance type with Nitro Enclaves, 4 vCPUs or more, for example `m5.xlarge`. Burstable `t3` types have none.
- Turn on enclave support when the instance is launched.
- Amazon Linux 2023, with an EBS volume for Loki's files.
- An IAM role that may put objects in the S3 bucket and may call KMS for the enclave (step 3).

## 2. Enclaves and k3s

```bash
sudo dnf install -y aws-nitro-enclaves-cli aws-nitro-enclaves-cli-devel
# /etc/nitro_enclaves/allocator.yaml: cpu_count: 2, memory_mib: 4096
sudo systemctl enable --now nitro-enclaves-allocator.service
curl -sfL https://get.k3s.io | sh -
kubectl label node "$(hostname)" aws-nitro-enclaves-k8s-dp=enabled
```

Then install the Nitro Enclaves Kubernetes device plugin from AWS on the node. `deploy/k8s/server/ml-engine.yaml` asks for its resource `aws.ec2.nitro/nitro_enclaves`.

## 3. KMS and S3

- **KMS key.** The hub's IAM user may call `GenerateDataKey`. `Decrypt` is allowed only with the condition `kms:RecipientAttestation:ImageSha384` equal to the PCR0 of the published enclave image (C-05). Each new enclave image has a new PCR0, so the policy is updated with it.
- **S3 bucket.** Private, public access blocked, versioning on, encrypted with the KMS key. It holds the sealed photos and the model files.

## 4. Images

GitHub Actions builds the images and pushes them to GHCR:
- `backend-server`, the server.
- `ml-engine`, which holds `ml-engine.eif`, built with `nitro-cli build-enclave` from `ai-engine` and the enclave program.
- `live-demo`, the Dashboard (web app).

## 5. Secrets, settings, manifests

Nothing here is committed (C-10).

```bash
kubectl apply -f deploy/k8s/namespaces.yaml
kubectl create secret generic app -n sweg9 --from-literal=HUB_TOKEN=...
kubectl create secret generic grafana -n logging --from-literal=GRAFANA_ADMIN_PASSWORD=...
kubectl create secret generic loki-push -n logging --from-literal=users='hub:<htpasswd hash>'
kubectl create secret tls nova-tls -n sweg9 --cert=... --key=...      # and the same in logging
kubectl create configmap server-settings -n sweg9 --from-file=settings/server.yaml
kubectl create configmap enclave-settings -n sweg9 --from-file=settings/risk.yaml --from-file=settings/enclave.yaml
kubectl apply -k deploy/k8s
```

## 6. The hub

On the laptop at home, with AWS credentials that may only call `GenerateDataKey` on the KMS key:

```bash
HUB_TOKEN=... APP_PASSWORD_HASH=... uv run backend hub
```

An Alloy agent on the same laptop pushes `data/hub/hub.log` to `https://nova.example.com/loki/api/v1/push` with the `loki-push` user.
