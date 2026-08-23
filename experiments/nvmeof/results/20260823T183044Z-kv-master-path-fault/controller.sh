#!/usr/bin/env bash
set -Eeuo pipefail

: "${RUN_ID:?set RUN_ID}"
[[ $RUN_ID =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
label="$RUN_ID-kv-master-path-fault"
client=intel-bigmem-2
target=intel-bigmem
helper=/tmp/mooncake-failure-client-helper.sh
probe=/tmp/mooncake-kv-master-path-fault-probe.py
remote_out="/sharenvme/userhome/zhouxulin/mooncake-kv-failure-results/$label"
target_rpc=/sharenvme/userhome/zhouxl/mooncake-nof-phase1/spdk/scripts/rpc.py
restore_needed=0

client_action() {
  local action=$1
  shift
  ssh -o BatchMode=yes "$client" env RUN_ID="$RUN_ID" ACTION="$action" "$@" bash "$helper"
}

target_snapshot() {
  local when=$1
  ssh -o BatchMode=yes "$target" sudo -n systemctl show mooncake-nof-spdk.service \
    -p MainPID -p ActiveState -p SubState -p ActiveEnterTimestampMonotonic \
    >"/tmp/$label-target-$when.service.txt"
  ssh -o BatchMode=yes "$target" sudo -n "$target_rpc" nvmf_get_subsystems \
    >"/tmp/$label-target-$when.subsystems.json"
  ssh -o BatchMode=yes "$target" sudo -n "$target_rpc" bdev_get_iostat \
    >"/tmp/$label-target-$when.iostat.json"
  ssh -o BatchMode=yes "$target" sudo -n "$target_rpc" bdev_nvme_get_controller_health_info -c Nvme0 \
    >"/tmp/$label-target-$when.smart.json"
  scp -q "/tmp/$label-target-$when.service.txt" "/tmp/$label-target-$when.subsystems.json" \
    "/tmp/$label-target-$when.iostat.json" "/tmp/$label-target-$when.smart.json" \
    "$client:$remote_out/inventory/"
}

restore() {
  local rc=0
  client_action start-master POLICY=round_robin LABEL=final-round-robin || rc=1
  client_action register LABEL=final-round-robin || rc=1
  client_action recovery-smoke || rc=1
  return "$rc"
}

ensure_rule_absent() {
  local comment=$1
  ssh -o BatchMode=yes "$client" bash -s -- "$comment" <<'EOF'
set -eu
comment=$1
for _ in 1 2 3; do
  rules=$(sudo -n iptables -S OUTPUT)
  if grep -Fq -- "$comment" <<<"$rules"; then
    sudo -n iptables -D OUTPUT -p tcp -d 10.0.0.34 --dport 50051 -m comment --comment "$comment" -j DROP 2>/dev/null || true
  else
    exit 0
  fi
done
rules=$(sudo -n iptables -S OUTPUT)
! grep -Fq -- "$comment" <<<"$rules"
EOF
}

on_exit() {
  local rc=$?
  trap - EXIT
  for trial_mode in 1-direct 2-transparent 3-transparent 4-direct 5-direct 6-transparent; do
    if ! ensure_rule_absent "mooncake-kv-master-fault-$RUN_ID-${trial_mode%-*}-${trial_mode#*-}"; then rc=1; fi
  done
  if (( restore_needed )) && ! restore; then rc=1; fi
  exit "$rc"
}
trap on_exit EXIT

scp -q /tmp/mooncake-failure-client-helper.sh /tmp/mooncake-kv-master-path-fault-probe.py \
  /tmp/mooncake-master-path-fault-design.json "$client:/tmp/"
client_action preflight
scp -q /tmp/mooncake-run-master-path-fault.sh "$client:$remote_out/harness/"
target_snapshot before
restore_needed=1
client_action start-master POLICY=remote_only LABEL=remote-fault-phase
client_action register LABEL=remote-fault-phase
client_action policy-smoke

modes=(direct transparent transparent direct direct transparent)
for index in "${!modes[@]}"; do
  trial=$((index + 1))
  client_action run-trial TRIAL="$trial" MODE="${modes[$index]}"
  sleep 2
done

client_action start-master POLICY=round_robin LABEL=final-round-robin
client_action register LABEL=final-round-robin
client_action recovery-smoke
restore_needed=0
client_action final-inventory
target_snapshot after
cmp -s "/tmp/$label-target-before.service.txt" "/tmp/$label-target-after.service.txt"
cmp -s "/tmp/$label-target-before.subsystems.json" "/tmp/$label-target-after.subsystems.json"
ssh -o BatchMode=yes "$client" tar -C "$(dirname "$remote_out")" -czf "$remote_out.raw-artifacts.tar.gz" "$(basename "$remote_out")"
