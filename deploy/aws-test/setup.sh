#!/bin/bash
# Copies bootstrap.sh to the test instance and runs it there (output also in /tmp/sweg9-aws-test-setup.log),
# then sets the KMS key policy to the PCR0 it printed.
set -euo pipefail
cd "$(dirname "$0")"
IP=${1:?usage: setup.sh <instance address>}
PEM=${NOVA_PEM:-$HOME/Downloads/NOVA.pem}   # the key pair NOVA; set NOVA_PEM if it is elsewhere
[ -f "$PEM" ] || { echo "no key file at $PEM: set NOVA_PEM" >&2; exit 1; }
SSH="ssh -i $PEM -o StrictHostKeyChecking=accept-new ec2-user@$IP"
for _ in $(seq 30); do $SSH true 2>/dev/null && break; sleep 5; done
scp -i "$PEM" -o StrictHostKeyChecking=accept-new bootstrap.sh "ec2-user@$IP:/tmp/bootstrap.sh"
$SSH 'bash /tmp/bootstrap.sh' 2>&1 | tee /tmp/sweg9-aws-test-setup.log
PCR0=$(grep -o '^PCR0=.*' /tmp/sweg9-aws-test-setup.log | tail -1 | cut -d= -f2)
[ -n "$PCR0" ] || { echo "no PCR0 in the output: setup failed, see /tmp/sweg9-aws-test-setup.log" >&2; exit 1; }
./key-policy.sh "$PCR0"
echo "next: ./hub-test.sh $IP   (HUB_TOKEN is the last-but-one line of /tmp/sweg9-aws-test-setup.log)"
