#!/bin/bash
# Creates the dev instance (t3.small, Seoul) and leaves it STOPPED: the first boot sets it up and powers
# off on its own (user-data.sh). If that takes over 15 minutes, this stops it anyway.
# Needs: `aws login`, key pair NOVA, security group NOVA-sg.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_REGION=${AWS_REGION:-ap-northeast-2}
NAME=sweg9-dev

if aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
     "Name=instance-state-name,Values=pending,running,stopping,stopped" \
     --query 'Reservations[].Instances[].InstanceId' --output text | grep -q .; then
  echo "$NAME already exists" >&2; exit 1
fi

AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)
SG=$(aws ec2 describe-security-groups --group-names NOVA-sg --query 'SecurityGroups[0].GroupId' --output text)
TAGS="{Key=Name,Value=$NAME},{Key=Project,Value=sweg9}"

ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type t3.small --key-name NOVA \
  --security-group-ids "$SG" --user-data file://user-data.sh \
  --instance-initiated-shutdown-behavior stop \
  --credit-specification CpuCredits=standard \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=12,VolumeType=gp3,DeleteOnTermination=true}' \
  --tag-specifications "ResourceType=instance,Tags=[$TAGS]" "ResourceType=volume,Tags=[$TAGS]" \
  --query 'Instances[0].InstanceId' --output text)
echo "launched $ID; waiting for the first boot to set it up and power off"

STATE=pending
for _ in $(seq 60); do
  sleep 15
  STATE=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name' --output text)
  [ "$STATE" = stopped ] && break
done
if [ "$STATE" != stopped ]; then
  echo "setup did not finish in 15 minutes; stopping $ID anyway" >&2
  aws ec2 stop-instances --instance-ids "$ID" >/dev/null
  aws ec2 wait instance-stopped --instance-ids "$ID"
fi
echo "$ID is stopped"
