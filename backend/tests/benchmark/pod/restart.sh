#!/usr/bin/env bash
# Run from backend/: tests/benchmark/pod/restart.sh NAME
# Start a stopped AWS pod, retrying every 10 minutes while its zone has no capacity (a stopped instance stays there).
name=$1
id=$(python3 -c "import json; print(json.load(open('../pod-$name.json'))['id'])")
until out=$(AWS_PAGER="" aws ec2 start-instances --region us-east-1 --instance-ids "$id" 2>&1); do
  echo "$(date +%H:%M) $name: $(echo "$out" | grep -oE 'InsufficientInstanceCapacity|Could not connect|[A-Za-z]+Exception' | head -1), retrying in 10 min"
  sleep 600
done
echo "$(date +%H:%M) $name: started"
