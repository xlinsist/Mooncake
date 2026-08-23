#!/usr/bin/env bash
set -Eeuo pipefail

: "${RUN_ID:?set RUN_ID}"
: "${REQUESTS:=500}"
: "${CAPACITY_PAGES:=64}"
: "${BLOCK_SIZE:=131072}"

base=/sharenvme/userhome/zhouxulin/mooncake-public-trace-store-runs
run_root="$base/$RUN_ID-public-trace-store-r${REQUESTS}"
scripts="$run_root/scripts"
out="$run_root/results"
build=/sharenvme/userhome/zhouxulin/mooncake-kv-e82f0bb7/build-nof
master="$build/mooncake-store/src/mooncake_master"
binding="$build/mooncake-integration"
trace_root=/sharenvme/userhome/zhouxulin/mooncake-kv-e82f0bb7/FAST25-release/traces
local_store=/mnt/datassd/zhouxulin-minipool/transparent-phase4
required_cases=no_store,direct-local,transparent-local,direct-remote,transparent-remote
restore_needed=1

[[ $RUN_ID =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
(( REQUESTS > 0 ))
(( CAPACITY_PAGES > 0 ))
(( BLOCK_SIZE > 0 && BLOCK_SIZE % 512 == 0 ))
[[ -d $scripts ]]
[[ -f $scripts/kv_workload.py ]]
[[ -f $scripts/public_trace_workload.py ]]
[[ -f $scripts/correctness.py ]]
[[ -x $master ]]
[[ -d $binding ]]
[[ -d $local_store ]]
mkdir -p "$out"/{cells,inventory,service-logs,register,smoke}
exec > >(tee -a "$out/driver.log") 2>&1

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
  echo "port $port did not become $expected" >&2
  return 1
}

stop_master() {
  local pids
  pids=$(pgrep -x mooncake_master || true)
  if [[ -n $pids ]]; then
    while read -r pid; do
      [[ -z $pid ]] || sudo -n kill "$pid"
    done <<<"$pids"
  fi
  wait_for_port 50051 down
  wait_for_port 9003 down
}

start_master() {
  local policy=$1 label=$2 log="$out/service-logs/master-$2.log"
  stop_master
  nohup sudo -n env MC_HETERO_STORAGE_POLICY="$policy" \
    "$master" --rpc_address=10.0.0.34 --enable_offload=true --logtostderr=true \
    >"$log" 2>&1 </dev/null &
  echo "$!" >"$out/service-logs/master-$label.launcher-pid"
  wait_for_port 50051 up
  wait_for_port 9003 up
  local pid owner actual_policy
  pid=$(pgrep -n -x mooncake_master)
  owner=$(ps -o user= -p "$pid" | xargs)
  actual_policy=$(sudo -n sh -c "tr '\0' '\n' </proc/$pid/environ" | sed -n 's/^MC_HETERO_STORAGE_POLICY=//p')
  [[ $owner == root ]]
  [[ $actual_policy == "$policy" ]]
  printf '%s %s %s %s\n' "$(date -u +%FT%TZ)" "$pid" "$owner" "$actual_policy" \
    >>"$out/inventory/master-transitions.txt"
}

store_env_prefix=(
  sudo -n env \
    PYTHONPATH="$binding" \
    TARGET_ADDR=10.0.0.5 \
    TARGET_NVME_SERIAL=PHAL13400005400AGN \
    CLIENT_RDMA_DEVICE=mlx5_0 \
    LOCAL_HOSTNAME=10.0.0.34 \
    METADATA_URL=http://10.0.0.34:8080/metadata \
    MASTER_ADDR=10.0.0.34:50051 \
    GLOBAL_SEGMENT_SIZE=1073741824 \
    LOCAL_BUFFER_SIZE=1073741824 \
    NQN=nqn.2026-08.local.mooncake:nof-phase1 \
    NSID=1 TRSVCID=4420 TEST_BYTES=68719476736 \
    TRANSPARENT_RUN_ID="$RUN_ID"
)

store_env() {
  local enable_ssd=$1
  shift
  "${store_env_prefix[@]}" ENABLE_SSD_OFFLOAD="$enable_ssd" \
    SSD_OFFLOAD_PATH="$local_store" "$@"
}

register_nof() {
  local label=$1 dir="$out/register/$1"
  mkdir -p "$dir"
  set +e
  store_env 0 python3 -c \
    'import os; from mooncake.store import MooncakeDistributedNoFRegister as R; rc=R().real_register(os.environ["NQN"], int(os.environ["NSID"]), os.environ["TARGET_ADDR"], int(os.environ["TRSVCID"]), 0, int(os.environ["TEST_BYTES"]), os.environ["MASTER_ADDR"]); raise SystemExit(rc)' \
    >"$dir/register.log" 2>&1
  local rc=$?
  set -e
  printf '%s\n' "$rc" >"$dir/register.rc"
  [[ $rc -eq 0 ]]
}

check_master_log() {
  local label=$1 log="$out/service-logs/master-$1.log"
  [[ -f $log ]]
  if grep -E 'nof_heartbeat_failure|unmount_nof_segment_by_heartbeat' "$log"; then
    echo "Master heartbeat/unmount failure in $label" >&2
    return 1
  fi
  printf '%s heartbeat-clean\n' "$(date -u +%FT%TZ)" \
    >"$out/service-logs/master-$label.check.txt"
}

cell_dir() {
  printf '%s/cells/%s-trial%s\n' "$out" "$1" "$2"
}

convert_cell() {
  local scenario=$1 trial=$2 cell source
  cell=$(cell_dir "$scenario" "$trial")
  source="$trace_root/${scenario}_trace.jsonl"
  mkdir -p "$cell"
  python3 "$scripts/public_trace_workload.py" "$source" "$cell" \
    --requests "$REQUESTS" --capacity-pages "$CAPACITY_PAGES" \
    --block-size "$BLOCK_SIZE" \
    --run-id "$RUN_ID-r$REQUESTS-$scenario-trial$trial" \
    >"$cell/convert.log" 2>&1
}

run_case() {
  local scenario=$1 trial=$2 mode=$3 target=$4 case_id=$5 cell enable_ssd=0
  cell=$(cell_dir "$scenario" "$trial")
  [[ $target == local_nvme ]] && enable_ssd=1
  local command=(python3 "$scripts/kv_workload.py" replay \
    "$cell/trace.jsonl" "$cell/raw-$case_id.json" \
    --mode "$mode" --case-id "$case_id" \
    --manifest "$cell/manifest.json" \
    --run-id "$RUN_ID-r$REQUESTS-$scenario-trial$trial" --recompute-us 1000)
  [[ -n $target ]] && command+=(--target "$target")
  set +e
  if [[ $mode == no_store ]]; then
    timeout --signal=TERM --kill-after=30s 600s /usr/bin/time -v \
      -o "$cell/$case_id.time.txt" "${command[@]}" \
      >"$cell/$case_id.log" 2>&1
  else
    timeout --signal=TERM --kill-after=30s 600s /usr/bin/time -v \
      -o "$cell/$case_id.time.txt" "${store_env_prefix[@]}" \
      ENABLE_SSD_OFFLOAD="$enable_ssd" SSD_OFFLOAD_PATH="$local_store" \
      "${command[@]}" \
      >"$cell/$case_id.log" 2>&1
  fi
  local rc=$?
  set -e
  printf '%s\n' "$rc" >"$cell/$case_id.rc"
  [[ $rc -eq 0 ]]
  python3 - "$cell/raw-$case_id.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
if result.get("status") != "pass" or result.get("errors"):
    raise SystemExit(f"failed replay result: {result.get('errors')}")
PY
}

run_pair() {
  local scenario=$1 trial=$2 target=$3 first=direct second=transparent
  if (( (trial + (${#scenario} % 2)) % 2 == 0 )); then
    first=transparent
    second=direct
  fi
  run_case "$scenario" "$trial" "$first" "$target" "$first-${target%%_*}"
  run_case "$scenario" "$trial" "$second" "$target" "$second-${target%%_*}"
}

summarize_cell() {
  local scenario=$1 trial=$2 cell
  cell=$(cell_dir "$scenario" "$trial")
  python3 "$scripts/kv_workload.py" summarize "$cell" \
    --required-case no_store \
    --required-case direct-local \
    --required-case transparent-local \
    --required-case direct-remote \
    --required-case transparent-remote \
    >"$cell/summarize.log" 2>&1
  python3 - "$cell/conclusion.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
if result.get("status") != "pass" or result.get("errors"):
    raise SystemExit(f"inconclusive cell: {result.get('errors')}")
PY
}

recovery_smoke() {
  local dir="$out/smoke/final-round-robin"
  mkdir -p "$dir"
  python3 "$scripts/kv_workload.py" generate "$dir" \
    --requests 1 --blocks-per-request 1 --block-size "$BLOCK_SIZE" \
    --reuse-ratio 0 --concurrency 1 --policy round_robin --seed 0 \
    --run-id "$RUN_ID-final-recovery" >"$dir/generate.log" 2>&1
  set +e
  timeout --signal=TERM --kill-after=30s 600s /usr/bin/time -v \
    -o "$dir/transparent.time.txt" "${store_env_prefix[@]}" \
    ENABLE_SSD_OFFLOAD=1 SSD_OFFLOAD_PATH="$local_store" \
    python3 "$scripts/kv_workload.py" replay "$dir/trace.jsonl" \
    "$dir/raw-transparent.json" --mode transparent --case-id transparent \
    --manifest "$dir/manifest.json" --run-id "$RUN_ID-final-recovery" \
    >"$dir/transparent.log" 2>&1
  local rc=$?
  set -e
  printf '%s\n' "$rc" >"$dir/transparent.rc"
  [[ $rc -eq 0 ]]
  python3 - "$dir/raw-transparent.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
if result.get("status") != "pass" or result.get("errors"):
    raise SystemExit(f"failed recovery smoke: {result.get('errors')}")
targets = {op["descriptor"]["target"] for op in result["operations"] if op.get("descriptor")}
if not targets <= {"local_nvme", "remote_nof"} or not targets:
    raise SystemExit(f"unexpected recovery target set: {targets}")
PY
}

restore_round_robin() {
  set +e
  start_master round_robin final-round-robin
  local master_rc=$?
  register_nof final-round-robin
  local register_rc=$?
  recovery_smoke
  local smoke_rc=$?
  check_master_log final-round-robin
  local log_rc=$?
  ps -eo user,pid,ppid,args | grep '[m]ooncake_master' \
    >"$out/inventory/final-master.txt"
  ss -ltn | grep -E ':(50051|9003) ' >>"$out/inventory/final-master.txt"
  nvme list-subsys >"$out/inventory/client-nvme-subsystems.after.txt"
  date -u +%FT%TZ >"$out/inventory/restored-utc.txt"
  set -e
  return $((master_rc || register_rc || smoke_rc || log_rc))
}

on_exit() {
  local rc=$?
  trap - EXIT
  if (( restore_needed )); then
    restore_round_robin || rc=1
  fi
  printf '%s\n' "$rc" >"$out/driver.rc"
  exit "$rc"
}
trap on_exit EXIT

date -u +%FT%TZ >"$out/inventory/started-utc.txt"
hostname >"$out/inventory/hostname.txt"
sha256sum "$scripts"/*.py "$master" "$binding"/mooncake/store*.so \
  >"$out/inventory/artifact-sha256.txt"
sha256sum "$trace_root"/conversation_trace.jsonl "$trace_root"/toolagent_trace.jsonl \
  >"$out/inventory/source-trace-sha256.txt"
wc -l "$trace_root"/conversation_trace.jsonl "$trace_root"/toolagent_trace.jsonl \
  >"$out/inventory/source-trace-lines.txt"
ps -eo user,pid,ppid,args | grep '[m]ooncake_master' >"$out/inventory/initial-master.txt"
ss -ltn | grep -E ':(50051|9003|8080) ' >>"$out/inventory/initial-master.txt"
initial_master_pid=$(pgrep -n -x mooncake_master)
[[ $(ps -o user= -p "$initial_master_pid" | xargs) == root ]]
initial_master_policy=$(sudo -n sh -c "tr '\0' '\n' </proc/$initial_master_pid/environ" | sed -n 's/^MC_HETERO_STORAGE_POLICY=//p')
[[ $initial_master_policy == round_robin ]]
printf '%s\n' "$initial_master_policy" >"$out/inventory/initial-master-policy.txt"
nvme list-subsys >"$out/inventory/client-nvme-subsystems.before.txt"
findmnt -no SOURCE,TARGET,FSTYPE -T "$local_store" >"$out/inventory/local-store-mount.txt"
python3 - "$binding" <<'PY' >"$out/inventory/binding-capabilities.txt"
import sys
sys.path.insert(0, sys.argv[1])
from mooncake.store import ReplicateConfig
print(f"local_replica_num={hasattr(ReplicateConfig(), 'local_replica_num')}")
PY
grep -Fxq 'local_replica_num=True' "$out/inventory/binding-capabilities.txt"
[[ $(nvme list-subsys | grep -c 'nqn.2026-08.local.mooncake:nof-phase1') -eq 1 ]]

cat >"$out/design.json" <<EOF
{
  "run_id": "$RUN_ID",
  "scenarios": ["conversation", "toolagent"],
  "requests": $REQUESTS,
  "capacity_pages": $CAPACITY_PAGES,
  "block_size": $BLOCK_SIZE,
  "trials": 3,
  "cases_per_trial": ["no_store", "direct-local", "transparent-local", "direct-remote", "transparent-remote"],
  "planned_cases": 30,
  "timeouts_seconds": {"case": 600, "kill_grace": 30},
  "claim_boundary": "Sequential FAST'25 hash-id replay through Mooncake Store; no arrival timing, true concurrency, model serving, transport-only, or system-superiority claim."
}
EOF

for trial in 1 2 3; do
  if [[ $trial == 2 ]]; then scenarios=(toolagent conversation); else scenarios=(conversation toolagent); fi
  for scenario in "${scenarios[@]}"; do
    echo "convert/no-store $scenario trial $trial"
    convert_cell "$scenario" "$trial"
    run_case "$scenario" "$trial" no_store '' no_store
  done
done

start_master local_only local-phase
register_nof local-phase
for trial in 1 2 3; do
  if [[ $trial == 2 ]]; then scenarios=(toolagent conversation); else scenarios=(conversation toolagent); fi
  for scenario in "${scenarios[@]}"; do
    echo "local $scenario trial $trial"
    run_pair "$scenario" "$trial" local_nvme
  done
done
check_master_log local-phase

start_master remote_only remote-phase
register_nof remote-phase
for trial in 1 2 3; do
  if [[ $trial == 2 ]]; then scenarios=(toolagent conversation); else scenarios=(conversation toolagent); fi
  for scenario in "${scenarios[@]}"; do
    echo "remote $scenario trial $trial"
    register_nof "before-$scenario-trial$trial"
    run_pair "$scenario" "$trial" remote_nof
  done
done
check_master_log remote-phase

for trial in 1 2 3; do
  for scenario in conversation toolagent; do
    echo "summarize $scenario trial $trial"
    summarize_cell "$scenario" "$trial"
  done
done

restore_round_robin
restore_needed=0
date -u +%FT%TZ >"$out/inventory/completed-utc.txt"
printf '0\n' >"$out/driver.rc"
printf '%s\n' "$run_root"
