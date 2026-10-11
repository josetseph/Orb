#!/usr/bin/env bash
# Run from backend/: tests/benchmark/pod/launch.sh NAME "UP ARGS" SPEC...
# Rent an AWS pod (retrying every 10 min while there is no capacity), queue the specs on it, then follow it.
name=$1; up_args=$2; shift 2
P=".venv/bin/python -u tests/benchmark/pod.py"
until $P --cloud aws --name "$name" up $up_args; do
  [ -f "../pod-$name.json" ] && { echo "$name: launched but setup failed"; exit 1; }
  echo "$(date +%H:%M) $name: no capacity, retrying in 10 min"; sleep 600
done
$P --name "$name" run "$@" && $P --name "$name" follow
echo "$name follow exit $?"
