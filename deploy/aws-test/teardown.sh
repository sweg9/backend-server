#!/bin/bash
# Deletes everything launch.sh made: the instance and its disk, the role, and the KMS key (AWS keeps a key
# 7 days before deleting it; photos sealed with it can never be opened after that).
set -euo pipefail
export AWS_REGION=${AWS_REGION:-ap-northeast-2} AWS_PAGER=""
NAME=sweg9-aws-test
ROLE=sweg9-aws-test-server
IDS=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$IDS" ]; then
  aws ec2 terminate-instances --instance-ids $IDS >/dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS
  echo "terminated $IDS (the disk went with it)"
fi
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam remove-role-from-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" 2>/dev/null || true
  aws iam delete-instance-profile --instance-profile-name "$ROLE" 2>/dev/null || true
  aws iam delete-role-policy --role-name "$ROLE" --policy-name kms-decrypt 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE"
  echo "deleted the role $ROLE"
fi
KEY=$(aws kms describe-key --key-id alias/sweg9-aws-test-photos --query KeyMetadata.KeyId --output text 2>/dev/null || true)
if [ -n "$KEY" ] && [ "$KEY" != None ]; then
  aws kms delete-alias --alias-name alias/sweg9-aws-test-photos || true   # the key's deletion takes it anyway
  aws kms schedule-key-deletion --key-id "$KEY" --pending-window-in-days 7 >/dev/null
  echo "KMS key $KEY is deleted in 7 days"
fi
rm -f /tmp/sweg9-aws-test-setup.log
aws ec2 describe-instances --filters "Name=tag:Project,Values=sweg9" \
  --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,State.Name]' --output text
