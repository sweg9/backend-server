#!/bin/bash
# A short test of the AWS design on one m6i.xlarge, deleted right after with teardown.sh (README.md here).
# Makes: a KMS key for sealed photos, an instance role for the server, and the instance with Nitro Enclaves.
# Needs: `aws login`, key pair NOVA, security group NOVA-sg. Costs about $0.25 an hour while it runs.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_REGION=${AWS_REGION:-ap-northeast-2} AWS_PAGER=""
NAME=sweg9-aws-test
ROLE=sweg9-aws-test-server
ALIAS=alias/sweg9-aws-test-photos
TAGS="{Key=Name,Value=$NAME},{Key=Project,Value=sweg9}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

if aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
     "Name=instance-state-name,Values=pending,running,stopping,stopped" \
     --query 'Reservations[].Instances[].InstanceId' --output text | grep -q .; then
  echo "$NAME already exists; run teardown.sh first" >&2; exit 1
fi

# SSH is open only to this computer's address (NOVA-sg); add it if it changed.
SG=$(aws ec2 describe-security-groups --group-names NOVA-sg --query 'SecurityGroups[0].GroupId' --output text)
MYIP=$(curl -fs https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" 2>/dev/null \
  && echo "SSH opened for $MYIP" || true

# The KMS key. Until bootstrap.sh prints the enclave's PCR0 only this account may use it; key-policy.sh then
# lets the instance role Decrypt only from that enclave image (C-05).
KEY=$(aws kms describe-key --key-id "$ALIAS" --query KeyMetadata.KeyId --output text 2>/dev/null || true)
if [ -z "$KEY" ] || [ "$KEY" = None ]; then
  KEY=$(aws kms create-key --description "sweg9 AWS test: sealed photos" --tags TagKey=Project,TagValue=sweg9 \
    --query KeyMetadata.KeyId --output text)
  aws kms create-alias --alias-name "$ALIAS" --target-key-id "$KEY"
fi

# The server's role: temporary credentials it hands the enclave for kmstool (aws_credentials: instance_role).
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --tags Key=Project,Value=sweg9 --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam put-role-policy --role-name "$ROLE" --policy-name kms-decrypt --policy-document \
    "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"kms:Decrypt\",\"Resource\":\"arn:aws:kms:$AWS_REGION:$ACCOUNT:key/$KEY\"}]}"
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  sleep 10  # a new instance profile takes a moment before EC2 accepts it
fi

AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)
ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type m6i.xlarge --key-name NOVA \
  --security-group-ids "$SG" --iam-instance-profile "Name=$ROLE" \
  --enclave-options Enabled=true \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2 \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=40,VolumeType=gp3,DeleteOnTermination=true}' \
  --tag-specifications "ResourceType=instance,Tags=[$TAGS]" "ResourceType=volume,Tags=[$TAGS]" \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo "instance  $ID  $IP"
echo "kms key   $KEY"
echo "next:     ./setup.sh $IP      (copies bootstrap.sh and runs it there, about 30 to 45 minutes)"
