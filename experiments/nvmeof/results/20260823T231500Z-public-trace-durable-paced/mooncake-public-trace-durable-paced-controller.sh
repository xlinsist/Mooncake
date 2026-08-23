#!/usr/bin/env bash
set -Eeuo pipefail

: "${RUN_ID:?set RUN_ID}"

client=intel-bigmem-2
target=intel-bigmem
target_rpc=/sharenvme/userhome/zhouxl/mooncake-nof-phase1/spdk/scripts/rpc.py
target_image=/sharenvme/userhome/zhouxl/mooncake-public-trace-paced-${RUN_ID}.img
target_bdev=TracePaced${RUN_ID//[^[:alnum:]]/}
target_nqn=nqn.2026-08.local.mooncake:trace-paced-${RUN_ID}
target_serial=TP${RUN_ID//[^[:digit:]]/}
target_serial=${target_serial:0:20}
client_mount=/mnt/mooncake-trace-paced-${RUN_ID}
client_remote_storage=${client_mount}/bench
client_local_storage=/mnt/datassd/zhouxulin-minipool/transparent-phase4/public-trace-paced-${RUN_ID}
client_result=/sharenvme/userhome/zhouxulin/mooncake-public-trace-results/${RUN_ID}-public-trace-durable-paced
source_benchmark_root=${SOURCE_BENCHMARK_ROOT:-$PWD/benchmarks/storage_benchmark_v1}
source_commit=$(git -C "$(dirname "$(dirname "$source_benchmark_root")")" rev-parse HEAD)
client_benchmark_parent=/sharenvme/userhome/zhouxulin/mooncake-public-trace-benchmark-runs/${RUN_ID}
benchmark_root=${client_benchmark_parent}/storage_benchmark_v1
trace_root=/sharenvme/userhome/zhouxulin/mooncake-kv-e82f0bb7/FAST25-release/traces
controller_log=/tmp/mooncake-public-trace-durable-paced-${RUN_ID}.log
service_pid=
created_bdev=0
created_subsystem=0
connected_client=0
mounted_client=0
staged_benchmark=0

exec > >(tee -a "$controller_log") 2>&1

target_cmd() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" "$@"
}

client_cmd() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$client" "$@"
}

target_rpc_cmd() {
  target_cmd sudo -n "$target_rpc" "$@"
}

client_device() {
  client_cmd bash -s -- "$target_nqn" <<'EOF'
set -Eeuo pipefail
nqn=$1
for controller_path in /sys/class/nvme/nvme[0-9]*; do
  [[ -f $controller_path/subsysnqn ]] || continue
  [[ $(<"$controller_path/subsysnqn") == "$nqn" ]] || continue
  controller=${controller_path##*/}
  for device in /dev/${controller}n*; do
    [[ -b $device ]] || continue
    printf '%s\n' "$device"
    exit 0
  done
done
exit 1
EOF
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  set +e
  echo "cleanup begin rc=$rc"
  if (( mounted_client )); then
    client_cmd sudo -n umount "$client_mount"
  fi
  client_cmd sudo -n rmdir "$client_mount" 2>/dev/null || true
  if (( connected_client )); then
    client_cmd sudo -n nvme disconnect -n "$target_nqn"
  fi
  if (( created_subsystem )); then
    target_rpc_cmd nvmf_delete_subsystem "$target_nqn"
  fi
  if (( created_bdev )); then
    target_rpc_cmd bdev_aio_delete "$target_bdev"
  fi
  target_cmd rm -f -- "$target_image"
  client_cmd rm -rf -- "$client_local_storage"
  if (( staged_benchmark )); then
    client_cmd rm -rf -- "$client_benchmark_parent"
  fi

  target_rpc_cmd nvmf_get_subsystems >"/tmp/${RUN_ID}.target-subsystems.after.json"
  target_rpc_cmd bdev_get_bdevs >"/tmp/${RUN_ID}.target-bdevs.after.json"
  target_cmd sudo -n systemctl show mooncake-nof-spdk.service -p MainPID -p ActiveState -p SubState --no-pager \
    >"/tmp/${RUN_ID}.target-service.after.txt"
  client_cmd nvme list-subsys -o json >"/tmp/${RUN_ID}.client-subsystems.after.json"

  if client_cmd test -d "$client_result"; then
    scp -q "/tmp/${RUN_ID}.target-subsystems.after.json" "$client:$client_result/meta/target-subsystems.after.json"
    scp -q "/tmp/${RUN_ID}.target-bdevs.after.json" "$client:$client_result/meta/target-bdevs.after.json"
    scp -q "/tmp/${RUN_ID}.target-service.after.txt" "$client:$client_result/meta/target-service.after.txt"
    scp -q "/tmp/${RUN_ID}.client-subsystems.after.json" "$client:$client_result/meta/client-subsystems.after.json"
    scp -q "$controller_log" "$client:$client_result/controller.log"
  fi

  if [[ -s /tmp/${RUN_ID}.target-subsystems.before.json && -s /tmp/${RUN_ID}.target-subsystems.after.json ]]; then
    jq -S . "/tmp/${RUN_ID}.target-subsystems.before.json" >"/tmp/${RUN_ID}.target-subsystems.before.canonical.json"
    jq -S . "/tmp/${RUN_ID}.target-subsystems.after.json" >"/tmp/${RUN_ID}.target-subsystems.after.canonical.json"
    cmp "/tmp/${RUN_ID}.target-subsystems.before.canonical.json" "/tmp/${RUN_ID}.target-subsystems.after.canonical.json" || rc=1
  fi
  if [[ -s /tmp/${RUN_ID}.target-bdevs.before.json && -s /tmp/${RUN_ID}.target-bdevs.after.json ]]; then
    jq -S . "/tmp/${RUN_ID}.target-bdevs.before.json" >"/tmp/${RUN_ID}.target-bdevs.before.canonical.json"
    jq -S . "/tmp/${RUN_ID}.target-bdevs.after.json" >"/tmp/${RUN_ID}.target-bdevs.after.canonical.json"
    cmp "/tmp/${RUN_ID}.target-bdevs.before.canonical.json" "/tmp/${RUN_ID}.target-bdevs.after.canonical.json" || rc=1
  fi
  current_pid=$(target_cmd sudo -n systemctl show mooncake-nof-spdk.service -p MainPID --value)
  [[ $current_pid == "$service_pid" ]] || rc=1
  target_cmd sudo -n systemctl is-active --quiet mooncake-nof-spdk.service || rc=1
  client_cmd test ! -e "$client_mount" || rc=1
  client_cmd test ! -e "$client_local_storage" || rc=1
  client_cmd test ! -e "$client_benchmark_parent" || rc=1
  target_cmd test ! -e "$target_image" || rc=1
  echo "cleanup complete rc=$rc"
  exit "$rc"
}
trap cleanup EXIT INT TERM

run_case() {
  local scenario=$1 requests=$2 trial=$3 path=$4 case_id=$5 storage_root
  if [[ $path == local ]]; then
    storage_root=$client_local_storage
  else
    storage_root=$client_remote_storage
  fi
  client_cmd bash -s -- \
    "$benchmark_root" "$trace_root" "$storage_root" "$scenario" "$requests" \
    "$case_id" "$client_result" <<'EOF'
set -Eeuo pipefail
benchmark_root=$1
trace_root=$2
storage_root=$3
scenario=$4
requests=$5
case_id=$6
result_root=$7
case_storage=$storage_root/$case_id
raw_dir=$result_root/raw
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p "$case_storage" "$raw_dir"
cleanup_case() { rm -rf -- "$case_storage"; }
trap cleanup_case EXIT
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 TORCH_NUM_THREADS=1
set +e
timeout --signal=TERM --kill-after=30s 900s /usr/bin/time -v \
  -o "$raw_dir/$case_id.time" \
  python3 "$benchmark_root/benchmark.py" \
    --scenario "$scenario" \
    --trace-dir "$trace_root" \
    --storage-dir "$case_storage" \
    --model glm5 \
    --page-size-tokens 512 \
    --max-requests "$requests" \
    --max-pages 64 \
    --fsync-mode always \
    --threads 1 \
    --replay-scales 1 \
    --progress-interval 0 \
    >"$raw_dir/$case_id.log" 2>&1
rc=$?
set -e
end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '%s\n' "$rc" >"$raw_dir/$case_id.rc"
if [[ $rc -eq 0 ]]; then
  grep -Eq '^  Scheduled Span:[[:space:]]+[0-9.]+ s$' "$raw_dir/$case_id.log"
  grep -Eq '^  Completion Lag:[[:space:]]+[0-9.]+ s$' "$raw_dir/$case_id.log"
  grep -Eq '^  Arrival Lag P50:[[:space:]]+[0-9.]+ ms$' "$raw_dir/$case_id.log"
  grep -Eq '^  Arrival Lag P95:[[:space:]]+[0-9.]+ ms$' "$raw_dir/$case_id.log"
  grep -Eq '^  Arrival Lag Max:[[:space:]]+[0-9.]+ ms$' "$raw_dir/$case_id.log"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$case_id" "$scenario" "$requests" "${case_id##*-}" "$start" "$end" "$rc" \
  >>"$result_root/cases.tsv"
exit "$rc"
EOF
}

echo "preflight $RUN_ID"
service_pid=$(target_cmd sudo -n systemctl show mooncake-nof-spdk.service -p MainPID --value)
[[ $service_pid =~ ^[1-9][0-9]*$ ]]
target_cmd sudo -n systemctl is-active --quiet mooncake-nof-spdk.service
target_cmd test ! -e "$target_image"
client_cmd test ! -e "$client_mount"
client_cmd test -d /mnt/datassd/zhouxulin-minipool/transparent-phase4
[[ -f $source_benchmark_root/benchmark.py ]]
client_cmd test -f "$trace_root/conversation_trace.jsonl"
client_cmd test -f "$trace_root/toolagent_trace.jsonl"

client_cmd test ! -e "$client_benchmark_parent"
client_cmd mkdir -p "$client_benchmark_parent"
staged_benchmark=1
scp -qr "$source_benchmark_root" "$client:$client_benchmark_parent/"
client_cmd test -f "$benchmark_root/benchmark.py"

target_rpc_cmd nvmf_get_subsystems >"/tmp/${RUN_ID}.target-subsystems.before.json"
target_rpc_cmd bdev_get_bdevs >"/tmp/${RUN_ID}.target-bdevs.before.json"
target_cmd sudo -n systemctl show mooncake-nof-spdk.service -p MainPID -p ActiveState -p SubState --no-pager \
  >"/tmp/${RUN_ID}.target-service.before.txt"
client_cmd nvme list-subsys -o json >"/tmp/${RUN_ID}.client-subsystems.before.json"

client_cmd mkdir -p "$client_result/meta" "$client_result/raw"
client_cmd bash -s -- "$client_result" "$benchmark_root" "$trace_root" "$RUN_ID" "$target_nqn" "$source_commit" <<'EOF'
set -Eeuo pipefail
result=$1
benchmark_root=$2
trace_root=$3
run_id=$4
target_nqn=$5
source_commit=$6
{
  date -u +%Y-%m-%dT%H:%M:%SZ
  hostname
  id
  uname -a
  printf 'SOURCE_COMMIT=%s\n' "$source_commit"
  sha256sum "$benchmark_root/benchmark.py" "$trace_root/conversation_trace.jsonl" "$trace_root/toolagent_trace.jsonl"
  wc -l "$trace_root/conversation_trace.jsonl" "$trace_root/toolagent_trace.jsonl"
  findmnt -rn -o SOURCE,FSTYPE,OPTIONS,TARGET /mnt/datassd
  df -h /mnt/datassd
  printf 'RUN_ID=%s\nTARGET_NQN=%s\n' "$run_id" "$target_nqn"
  printf 'OMP_NUM_THREADS=1\nOPENBLAS_NUM_THREADS=1\nMKL_NUM_THREADS=1\nNUMEXPR_NUM_THREADS=1\nVECLIB_MAXIMUM_THREADS=1\nTORCH_NUM_THREADS=1\n'
  printf 'model=glm5\npage_size_tokens=512\nmax_pages=64\nfsync_mode=always\nthreads=1\nreplay_scales=1\n'
} >"$result/meta/client-environment.txt"
printf 'case_id\tscenario\trequests\tpath\tstart_utc\tend_utc\trc\n' >"$result/cases.tsv"
EOF
scp -q "/tmp/${RUN_ID}.target-subsystems.before.json" "$client:$client_result/meta/target-subsystems.before.json"
scp -q "/tmp/${RUN_ID}.target-bdevs.before.json" "$client:$client_result/meta/target-bdevs.before.json"
scp -q "/tmp/${RUN_ID}.target-service.before.txt" "$client:$client_result/meta/target-service.before.txt"
scp -q "/tmp/${RUN_ID}.client-subsystems.before.json" "$client:$client_result/meta/client-subsystems.before.json"

cat >"/tmp/${RUN_ID}.design.json" <<EOF
{
  "run_id": "$RUN_ID",
  "scenarios": ["conversation", "toolagent"],
  "request_scales": [100],
  "paths": ["local_nvme_ext4", "file_backed_nvmeof_xfs"],
  "repetitions": 3,
  "model": "glm5",
  "page_size_tokens": 512,
  "max_pages": 64,
  "fsync_mode": "always",
  "threads": 1,
  "replay_scale": 1,
  "claim_boundary": "GPU-free durable storage-path arrival-debt evidence; different devices/filesystems, sequential single-thread replay, modulo page mapping, no matched-substrate, transport-only, serving, true-concurrency, or system-superiority claim."
}
EOF
scp -q "/tmp/${RUN_ID}.design.json" "$client:$client_result/design.json"

echo "create exact temporary target"
target_cmd truncate -s 8G "$target_image"
target_rpc_cmd bdev_aio_create "$target_image" "$target_bdev" 4096
created_bdev=1
target_rpc_cmd nvmf_create_subsystem "$target_nqn" -a -s "$target_serial" -d MooncakeTracePaced
created_subsystem=1
target_rpc_cmd nvmf_subsystem_add_ns "$target_nqn" "$target_bdev" -n 1
target_rpc_cmd nvmf_subsystem_add_listener "$target_nqn" -t rdma -a 10.0.0.5 -s 4420

client_cmd sudo -n nvme connect -t rdma -a 10.0.0.5 -s 4420 -n "$target_nqn"
connected_client=1
device=
for attempt in $(seq 1 20); do
  if device=$(client_device); then break; fi
  sleep 0.5
done
[[ -n $device ]]
echo "temporary device $device"
client_cmd sudo -n nvme id-ns "$device" >/dev/null
client_cmd sudo -n mkfs.xfs -f "$device" >/dev/null
client_cmd sudo -n mkdir -p "$client_mount"
client_cmd sudo -n mount "$device" "$client_mount"
mounted_client=1
client_cmd sudo -n chown zhouxulin:zhouxulin "$client_mount"
client_cmd mkdir -p "$client_remote_storage" "$client_local_storage"
client_cmd findmnt -rn -o SOURCE,FSTYPE,OPTIONS,TARGET "$client_mount" >"/tmp/${RUN_ID}.remote-mount.txt"
scp -q "/tmp/${RUN_ID}.remote-mount.txt" "$client:$client_result/meta/remote-mount.txt"

echo "run 12 durable paced cases"
for trial in 1 2 3; do
  case $trial in
    1) scenarios=(conversation toolagent) ;;
    2) scenarios=(toolagent conversation) ;;
    3) scenarios=(conversation toolagent) ;;
  esac
  scenario_index=0
  for scenario in "${scenarios[@]}"; do
    if (( (trial + scenario_index) % 2 )); then paths=(local remote); else paths=(remote local); fi
    for path in "${paths[@]}"; do
      case_id=${scenario}-r100-t${trial}-${path}
      echo "case $case_id"
      run_case "$scenario" 100 "$trial" "$path" "$case_id"
    done
    scenario_index=$((scenario_index + 1))
  done
done

client_cmd test "$(client_cmd find "$client_result/raw" -name '*.rc' -type f | wc -l)" -eq 12
client_cmd bash -s -- "$client_result" <<'EOF'
set -Eeuo pipefail
result=$1
find "$result/raw" -name '*.rc' -type f -print0 | xargs -0 -r grep -L '^0$' | grep . && exit 1 || true
EOF

echo "matrix complete; cleanup will verify invariants"
