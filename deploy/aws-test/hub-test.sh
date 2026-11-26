#!/bin/bash
# Runs the hub on this laptop against the test server: settings copied to a temporary folder with the server's
# address and the KMS key, and short-lived AWS credentials from `aws login` for GenerateDataKey. The camera is
# the laptop's. Asks for the HUB_TOKEN bootstrap.sh printed and an app password for this test.
set -euo pipefail
cd "$(dirname "$0")/../.."
export AWS_REGION=${AWS_REGION:-ap-northeast-2} AWS_PAGER=""
IP=${1:?usage: hub-test.sh <instance address>}
KEY=$(aws kms describe-key --key-id alias/sweg9-aws-test-photos --query KeyMetadata.Arn --output text)
DIR=$(mktemp -d)
cp settings/*.yaml "$DIR/"
sed -i '' -e "s#^server_url:.*#server_url: ws://$IP/api/ws/hub#" -e "s#^kms_key_id:.*#kms_key_id: $KEY#" \
  -e "s#^database_url:.*#database_url: sqlite:///$DIR/app.db#" "$DIR/hub.yaml"
export BACKEND_SETTINGS_DIR=$DIR
eval "$(aws configure export-credentials --format env)"
read -rsp "HUB_TOKEN from setup: " HUB_TOKEN; echo; export HUB_TOKEN
export APP_PASSWORD_HASH=$(uv run backend hash-password)
uv run backend seed-demo
echo "API: http://$IP/api   (sign in with the password you just chose)"
exec uv run backend hub --stdout
