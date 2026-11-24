#!/bin/bash
# Start, stop or check the dev instance:  deploy/dev-ec2/dev.sh start|stop|status
# Without an Elastic IP the address changes on every start; share the URL printed here.
set -euo pipefail
export AWS_REGION=${AWS_REGION:-ap-northeast-2}
ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=sweg9-dev \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
[ "$ID" != None ] || { echo "no sweg9-dev instance" >&2; exit 1; }

case "${1:-status}" in
  start) aws ec2 start-instances --instance-ids "$ID" >/dev/null
         aws ec2 wait instance-running --instance-ids "$ID" ;;
  stop)  aws ec2 stop-instances --instance-ids "$ID" >/dev/null
         aws ec2 wait instance-stopped --instance-ids "$ID" ;;
  status) ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
read -r STATE IP < <(aws ec2 describe-instances --instance-ids "$ID" \
  --query 'Reservations[0].Instances[0].[State.Name,PublicIpAddress]' --output text)
echo "$ID $STATE"
[ "$STATE" = running ] && echo "API: http://$IP   SSH: ssh -i ~/Downloads/NOVA.pem ec2-user@$IP"
exit 0
