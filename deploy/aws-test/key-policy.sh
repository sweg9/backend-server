#!/bin/bash
# The KMS key policy of the test (C-05): this account manages the key and the hub (here: you, signed in with
# `aws login`) may GenerateDataKey; the server's instance role may Decrypt only from the enclave image with this
# PCR0. In the real deployment the hub gets its own IAM user with GenerateDataKey only.
set -euo pipefail
export AWS_REGION=${AWS_REGION:-ap-northeast-2} AWS_PAGER=""
PCR0=${1:?usage: key-policy.sh <PCR0>}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
KEY=$(aws kms describe-key --key-id alias/sweg9-aws-test-photos --query KeyMetadata.KeyId --output text)
ROLE=arn:aws:iam::$ACCOUNT:role/sweg9-aws-test-server
aws kms put-key-policy --key-id "$KEY" --policy-name default --policy "$(cat <<JSON
{"Version": "2012-10-17", "Statement": [
  {"Sid": "Account manages the key", "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::$ACCOUNT:root"},
   "Action": ["kms:Describe*", "kms:Get*", "kms:List*", "kms:Put*", "kms:Create*", "kms:Update*", "kms:Enable*",
              "kms:Disable*", "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion",
              "kms:CancelKeyDeletion", "kms:GenerateDataKey"],
   "Resource": "*"},
  {"Sid": "Only the enclave opens photos", "Effect": "Allow", "Principal": {"AWS": "$ROLE"},
   "Action": "kms:Decrypt", "Resource": "*",
   "Condition": {"StringEqualsIgnoreCase": {"kms:RecipientAttestation:ImageSha384": "$PCR0"}}}
]}
JSON
)"
echo "key $KEY: Decrypt only from the enclave with PCR0 ${PCR0:0:16}..."
