#!/usr/bin/env bash
set -Eeuo pipefail

: "${RUN_ID:?set RUN_ID}"
: "${ACTION:?set ACTION}"
[[ $RUN_ID =~ ^[0-9]{8}T[0-9]{6}Z$ ]]

repo=/sharenvme/userhome/zhouxulin/mooncake-kv-e82f0bb7
run_sh="$repo/experiments/nvmeof/run.sh"
master="$repo/build-nof/mooncake-store/src/mooncake_master"
probe=/tmp/mooncake-kv-master-path-fault-probe.py
out="/sharenvme/userhome/zhouxulin/mooncake-kv-failure-results/$RUN_ID-kv-master-path-fault"
source "$repo/experiments/nvmeof/config.env"
mkdir -p "$out"/{harness,inventory,register,service-logs,smoke,trials}

wait_for_port() {
  local port=$1 expected=$2 attempt
  for attempt in $(seq 1 80); do
    if ss -ltn | grep -q ":$port "; then
      [[ $expected == up ]] && return 0
    else
      [[ $expected == down ]] && return 0
    fi
    sleep 0.5
  done
  return 1
}

stop_master() {
  local pid
  local -a master_processes
  if sudo -n ss -ltnpH 'sport = :50051 or sport = :9003' | grep -q .; then
    pid=$(master_listener_pid)
  else
    mapfile -t master_processes < <(pgrep -f "^$master( |$)" || true)
    [[ ${#master_processes[@]} -le 1 ]]
    pid=${master_processes[0]:-}
  fi
  [[ -z $pid ]] || sudo -n kill "$pid"
  wait_for_port 50051 down
  wait_for_port 9003 down
}

master_listener_pid() {
  local lines pid executable
  local -a listener_pids
  lines=$(sudo -n ss -ltnpH 'sport = :50051 or sport = :9003')
  [[ $(grep -c 'LISTEN' <<<"$lines") == 2 ]]
  grep -q ':50051 ' <<<"$lines"
  grep -q ':9003 ' <<<"$lines"
  mapfile -t listener_pids < <(sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' <<<"$lines" | sort -u)
  [[ ${#listener_pids[@]} == 1 ]]
  pid=${listener_pids[0]}
  executable=$(sudo -n readlink -f "/proc/$pid/exe")
  [[ $executable == "$master" ]]
  [[ $(ps -o user= -p "$pid" | xargs) == root ]]
  printf '%s\n' "$pid"
}

start_master() {
  local policy=$1 label=$2
  local log="$out/service-logs/master-$label.log"
  stop_master
  nohup sudo -n env MC_HETERO_STORAGE_POLICY="$policy" \
    "$master" --rpc_address=10.0.0.34 --enable_offload=true --logtostderr=true \
    >"$log" 2>&1 </dev/null &
  echo "$!" >"$out/service-logs/master-$label.launcher-pid"
  wait_for_port 50051 up
  wait_for_port 9003 up
  local pid owner actual_policy
  pid=$(master_listener_pid)
  owner=$(ps -o user= -p "$pid" | xargs)
  actual_policy=$(sudo -n sh -c "tr '\0' '\n' </proc/$pid/environ" | sed -n 's/^MC_HETERO_STORAGE_POLICY=//p')
  [[ $owner == root && $actual_policy == "$policy" ]]
  printf '%s %s %s %s\n' "$(date -u +%FT%TZ)" "$pid" "$owner" "$actual_policy" >>"$out/inventory/master-transitions.txt"
}

register_nof() {
  local label=$1
  local dir="$out/register/$label"
  mkdir -p "$dir"
  RESULT_DIR="$dir" "$run_sh" register >"$out/service-logs/register-$label.log" 2>&1
}

preflight() {
  [[ ! -e $out/inventory/started-utc.txt ]]
  [[ -z $(find "$out/trials" -mindepth 1 -maxdepth 1 -print -quit) ]]
  date -u +%FT%TZ >"$out/inventory/started-utc.txt"
  cp "$probe" /tmp/mooncake-failure-client-helper.sh /tmp/mooncake-master-path-fault-design.json "$out/harness/"
  hostname >"$out/inventory/hostname.txt"
  git -C "$repo" rev-parse HEAD >"$out/inventory/source-commit.txt"
  git -C "$repo" status --short >"$out/inventory/source-status.txt"
  sha256sum "$master" "$repo"/build-nof/mooncake-integration/mooncake/store*.so >"$out/inventory/build-sha256.txt"
  grep -Fxq '255736477145236f39702f44e7a523a36914828c' "$out/inventory/source-commit.txt"
  [[ ! -s $out/inventory/source-status.txt ]]
  grep -Fqx "275c6420ad69bf88a966d04593b602317409ead62ef298a8678a88b12c46c6f2  $master" "$out/inventory/build-sha256.txt"
  grep -Fq '5ce06aeaaa4c821295c80583fc8aaea3631bdfe376c0d935a22666a1d5affc8f' "$out/inventory/build-sha256.txt"
  ps -eo user,pid,ppid,lstart,args | grep '[m]ooncake_master' >"$out/inventory/master-before.txt"
  ss -ltn | grep -E ':(50051|9003|8080) ' >>"$out/inventory/master-before.txt"
  sudo -n iptables -S OUTPUT >"$out/inventory/iptables-before.txt"
  ! grep -Fq 'mooncake-kv-master-fault-' "$out/inventory/iptables-before.txt"
  local pid owner policy
  pid=$(master_listener_pid)
  owner=$(ps -o user= -p "$pid" | xargs)
  policy=$(sudo -n sh -c "tr '\0' '\n' </proc/$pid/environ" | sed -n 's/^MC_HETERO_STORAGE_POLICY=//p')
  [[ $owner == root && $policy == round_robin ]]
}

policy_smoke() {
  local dir="$out/smoke/remote-only"
  mkdir -p "$dir"
  RESULT_DIR="$dir" TRANSPARENT_RUN_ID="$RUN_ID-remote-smoke" "$run_sh" transparent-remote >"$dir/run.log" 2>&1
}

recovery_smoke() {
  local dir="$out/smoke/final-round-robin"
  mkdir -p "$dir"
  RESULT_DIR="$dir" TRANSPARENT_RUN_ID="$RUN_ID-final-recovery" "$run_sh" transparent-round-robin >"$dir/run.log" 2>&1
}

run_trial() (
  local trial=$1 mode=$2
  local trial_dir="$out/trials/trial$trial-$mode"
  local control="$trial_dir/control"
  local comment="mooncake-kv-master-fault-$RUN_ID-$trial-$mode" probe_pid rc=0 current_rules
  local rule=(-p tcp -d 10.0.0.34 --dport 50051 -m comment --comment "$comment" -j DROP)
  [[ ! -e $trial_dir ]]
  mkdir -p "$control"
  current_rules=$(sudo -n iptables -S OUTPUT)
  ! grep -Fq -- "$comment" <<<"$current_rules"
  cleanup_rule_best_effort() {
    sudo -n iptables -C OUTPUT "${rule[@]}" 2>/dev/null && sudo -n iptables -D OUTPUT "${rule[@]}" || true
  }
  clear_rule_strict() {
    sudo -n iptables -D OUTPUT "${rule[@]}"
    current_rules=$(sudo -n iptables -S OUTPUT)
    ! grep -Fq -- "$comment" <<<"$current_rules"
  }
  cleanup_trial() {
    cleanup_rule_best_effort
    if [[ -n ${probe_pid:-} ]]; then kill "$probe_pid" 2>/dev/null || true; fi
  }
  trap cleanup_trial EXIT
  export PYTHONPATH="$repo/build-nof/mooncake-integration:$repo/experiments/nvmeof${PYTHONPATH:+:$PYTHONPATH}"
  timeout --signal=TERM --kill-after=5s 120s sudo -n env \
    PYTHONPATH="$PYTHONPATH" TARGET_ADDR="$TARGET_ADDR" TARGET_NVME_SERIAL="$TARGET_NVME_SERIAL" \
    CLIENT_RDMA_DEVICE="$CLIENT_RDMA_DEVICE" LOCAL_HOSTNAME="$LOCAL_HOSTNAME" \
    METADATA_URL="$METADATA_URL" MASTER_ADDR="$MASTER_ADDR" GLOBAL_SEGMENT_SIZE="$GLOBAL_SEGMENT_SIZE" \
    LOCAL_BUFFER_SIZE="$LOCAL_BUFFER_SIZE" ENABLE_SSD_OFFLOAD=0 SSD_OFFLOAD_PATH= \
    MC_RPC_TIMEOUT_MS=1000 python3 "$probe" --run-id "$RUN_ID" --trial "$trial" --mode "$mode" \
    --control-dir "$control" --output "$trial_dir/result.json" --harness-dir "$repo/experiments/nvmeof" \
    >"$trial_dir/probe.log" 2>&1 &
  probe_pid=$!
  for _ in $(seq 1 300); do [[ -f $control/ready-to-fault ]] && break; kill -0 "$probe_pid" 2>/dev/null || break; sleep 0.1; done
  [[ -f $control/ready-to-fault ]]
  sudo -n iptables -I OUTPUT 1 "${rule[@]}"
  date -u +%s.%N >"$control/fault-started-epoch.txt"
  touch "$control/fault-active"
  sleep 3
  sudo -n iptables -nvx -L OUTPUT --line-numbers >"$trial_dir/iptables-during.txt"
  awk -v comment="$comment" '$0 ~ comment && $2 + 0 > 0 {matched=1} END {exit !matched}' "$trial_dir/iptables-during.txt"
  clear_rule_strict
  date -u +%s.%N >"$control/fault-cleared-epoch.txt"
  touch "$control/fault-cleared"
  wait "$probe_pid" || rc=$?
  probe_pid=
  printf '%s\n' "$rc" >"$trial_dir/probe.exitcode"
  [[ $rc == 0 || $rc == 1 ]]
  python3 - "$trial_dir/result.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["status"] in ("pass", "fail")
assert not data["evidence_failures"], data["evidence_failures"]
assert data["configured_concurrency"] == 1
assert data["fault_window_failures"] >= 1
assert data["post_fault_successes"] >= 8
assert not data["published_failed_puts"]
assert data["seed"]["verified_after"] and data["seed"]["descriptor_stable"]
allowed = {"failed put residue exceeded cleanup deadline"}
assert set(data["product_failures"]) <= allowed, data["product_failures"]
assert not data["post_close_incomplete_audits"]
assert not data["post_close_unsafe_failed_puts"]
assert not data["post_close_residue_deadline_failures"]
PY
  sudo -n iptables -S OUTPUT >"$trial_dir/iptables-after.txt"
  ! grep -Fq "$comment" "$trial_dir/iptables-after.txt"
)

final_inventory() {
  ps -eo user,pid,ppid,lstart,args | grep '[m]ooncake_master' >"$out/inventory/master-after.txt"
  ss -ltn | grep -E ':(50051|9003|8080) ' >>"$out/inventory/master-after.txt"
  sudo -n iptables -S OUTPUT >"$out/inventory/iptables-after.txt"
  ! grep -Fq 'mooncake-kv-master-fault-' "$out/inventory/iptables-after.txt"
  date -u +%FT%TZ >"$out/inventory/completed-utc.txt"
}

case "$ACTION" in
  preflight) preflight ;;
  start-master) start_master "$POLICY" "$LABEL" ;;
  register) register_nof "$LABEL" ;;
  policy-smoke) policy_smoke ;;
  run-trial) run_trial "$TRIAL" "$MODE" ;;
  recovery-smoke) recovery_smoke ;;
  final-inventory) final_inventory ;;
  *) echo "unknown ACTION=$ACTION" >&2; exit 2 ;;
esac
