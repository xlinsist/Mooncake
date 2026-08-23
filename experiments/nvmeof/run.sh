#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

: "${BENCH_IO_TIMEOUT:=30}"
: "${BENCH_KILL_GRACE:=5}"
: "${CHAR_IODEPTH:=32}"
: "${SAME_SSD_SERVICE:=mooncake-nof-spdk.service}"
: "${SAME_SSD_SIZES:=4096 65536 262144 1048576 4194304 16777216}"
: "${SAME_SSD_DEPTHS:=1 8 32}"
: "${SAME_SSD_REPETITIONS:=3}"
: "${SAME_SSD_WARMUP:=2}"
: "${SAME_SSD_RUNTIME:=15}"
: "${SAME_SSD_TARGET_CPU_MASK:=0xff}"
: "${SAME_SSD_CLIENT_CPUS:=0-7}"
: "${SAME_SSD_MAX_SRQ_DEPTH:=128}"
: "${SAME_SSD_INFLIGHT_BYTES:=8388608}"

export PYTHONPATH="$BUILD_DIR/mooncake-integration${PYTHONPATH:+:$PYTHONPATH}"

run_store_python() {
  sudo -n env PYTHONPATH="$PYTHONPATH" \
    TARGET_ADDR="$TARGET_ADDR" TARGET_NVME_SERIAL="$TARGET_NVME_SERIAL" \
    CLIENT_RDMA_DEVICE="$CLIENT_RDMA_DEVICE" LOCAL_HOSTNAME="$LOCAL_HOSTNAME" \
    METADATA_URL="$METADATA_URL" MASTER_ADDR="$MASTER_ADDR" \
    GLOBAL_SEGMENT_SIZE="$GLOBAL_SEGMENT_SIZE" \
    LOCAL_BUFFER_SIZE="$LOCAL_BUFFER_SIZE" \
    ENABLE_SSD_OFFLOAD="${ENABLE_SSD_OFFLOAD:-0}" \
    SSD_OFFLOAD_PATH="${SSD_OFFLOAD_PATH:-}" NQN="$NQN" NSID="$NSID" \
    TRSVCID="$TRSVCID" TEST_BYTES="$TEST_BYTES" \
    TRANSPARENT_RUN_ID="${TRANSPARENT_RUN_ID:-}" python3 "$@"
}

kv_workload_dir() {
  if [[ -n ${KV_WORKLOAD_RESULT_DIR:-} ]]; then
    printf '%s\n' "$KV_WORKLOAD_RESULT_DIR"
  else
    local run_id=${KV_WORKLOAD_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
    printf '%s\n' "$RESULT_DIR/kv-workload/$run_id"
  fi
}

kv_workload_generate() {
  local out
  out=$(kv_workload_dir)
  mkdir -p "$out"
  local args=("$(dirname "$0")/kv_workload.py" generate "$out" \
    --requests "${KV_WORKLOAD_REQUESTS:-12}" \
    --blocks-per-request "${KV_WORKLOAD_BLOCKS_PER_REQUEST:-4}" \
    --block-size "${KV_WORKLOAD_BLOCK_SIZE:-131072}" \
    --reuse-ratio "${KV_WORKLOAD_REUSE_RATIO:-0.5}" \
    --concurrency "${KV_WORKLOAD_CONCURRENCY:-1}" \
    --policy "${KV_WORKLOAD_POLICY:-round_robin}" \
    --seed "${KV_WORKLOAD_SEED:-0}")
  [[ -n ${KV_WORKLOAD_RUN_ID:-} ]] && args+=(--run-id "$KV_WORKLOAD_RUN_ID")
  python3 "${args[@]}"
}

kv_workload_replay() {
  local out trace manifest mode target case_id run_id
  out=$(kv_workload_dir)
  trace=${KV_WORKLOAD_TRACE:-$out/trace.jsonl}
  manifest=${KV_WORKLOAD_MANIFEST:-$out/manifest.json}
  mode=${KV_WORKLOAD_MODE:-no_store}
  target=${KV_WORKLOAD_TARGET:-}
  case_id=${KV_WORKLOAD_CASE_ID:-$mode${target:+-$target}}
  run_id=${KV_WORKLOAD_RUN_ID:-}
  [[ -f "$trace" ]] || die "KV workload trace is missing: $trace"
  mkdir -p "$out"
  local args=("$(dirname "$0")/kv_workload.py" replay "$trace" "$out/raw-$case_id.json"
    --mode "$mode" --case-id "$case_id" --recompute-us "${KV_WORKLOAD_RECOMPUTE_US:-1000}"
    --replay-scale "${KV_WORKLOAD_REPLAY_SCALE:-0}")
  [[ -f "$manifest" ]] && args+=(--manifest "$manifest")
  [[ -n $target ]] && args+=(--target "$target")
  [[ -n $run_id ]] && args+=(--run-id "$run_id")
  if [[ $mode == no_store ]]; then
    python3 "${args[@]}"
  else
    [[ -n ${TRANSPARENT_RUN_ID:-} || $mode == direct ]] || die "set TRANSPARENT_RUN_ID for Store replay"
    [[ $target != local_nvme ]] || ENABLE_SSD_OFFLOAD=1
    run_store_python "${args[@]}"
  fi
}

kv_workload_summarize() {
  local out
  out=${KV_WORKLOAD_RESULT_DIR:-$(kv_workload_dir)}
  python3 "$(dirname "$0")/kv_workload.py" summarize "$out" \
    ${KV_WORKLOAD_REQUIRED_CASES:+$(printf '%s\n' "$KV_WORKLOAD_REQUIRED_CASES" | tr ',' '\n' | sed 's/^/--required-case /')}
}

require_transparent_run_id() {
  : "${TRANSPARENT_RUN_ID:?set TRANSPARENT_RUN_ID to a unique acceptance batch ID}"
}

inventory_one() {
  local output=$1
  shift
  "$@" bash -s >"$output" 2>&1 <<'EOF'
set +e
uname -a
cat /etc/os-release
command -v nvme && nvme version
command -v fio && fio --version
command -v ibv_devices && ibv_devices
command -v ibv_devinfo && ibv_devinfo
command -v rdma && rdma link
ip -br address
lscpu
numactl --hardware
lsblk -f
nvme list
nvme list-subsys
lspci -Dnn | grep -i 'non-volatile memory'
cat /proc/meminfo | grep -i huge
EOF
}

run_fio_matrix() {
  need fio
  require_destructive_confirmation
  device_is_safe "$CLIENT_DEVICE"
  verify_serial
  mkdir -p "$RESULT_DIR/fio"
  for pattern in seq rand; do
    local block_size=131072
    [[ $pattern == rand ]] && block_size=4096
    for operation in read write; do
      for depth in 1 8 32 64; do
        for run in $(seq 1 "$REPETITIONS"); do
          name="$pattern-$operation-bs$block_size-qd$depth-run$run"
          rw=$operation
          [[ $pattern == rand ]] && rw="rand$operation"
          log "fio $name"
          sudo -n fio --name="$name" --filename="$CLIENT_DEVICE" --rw="$rw" \
            --bs="$block_size" --iodepth="$depth" --ioengine=libaio \
            --direct=1 --time_based=1 --runtime="$FIO_RUNTIME" \
            --ramp_time="$FIO_RAMP" --size="$TEST_BYTES" --group_reporting=1 \
            --randrepeat=1 --norandommap=1 --output-format=json+ \
            --output="$RESULT_DIR/fio/$name.json"
        done
      done
    done
  done
}

run_nof_matrix() {
  local bench="$BUILD_DIR/mooncake-store/benchmarks/nof_worker_pool_bench"
  [[ -x $bench ]] || die "benchmark binary missing: $bench"
  mkdir -p "$RESULT_DIR/nof" "$RESULT_DIR/telemetry"
  (rdma statistic show || true) >"$RESULT_DIR/telemetry/rdma-before.txt" 2>&1
  target "'$SPDK_DIR/scripts/rpc.py' bdev_nvme_get_controller_health_info 2>/dev/null || true" \
    >"$RESULT_DIR/telemetry/spdk-health-before.json"
  for pattern in seq rand; do
    local block_size=131072
    [[ $pattern == rand ]] && block_size=4096
    for operation in read write; do
      for depth in 1 8 32 64; do
        for run in $(seq 1 "$REPETITIONS"); do
          name="$pattern-$operation-bs$block_size-qd$depth-run$run"
          rw=$operation
          [[ $pattern == rand ]] && rw="rand$operation"
          log "NoF $name"
          if [[ -x /usr/bin/time ]]; then
            /usr/bin/time -v -o "$RESULT_DIR/telemetry/$name.time" \
              timeout --signal=TERM --kill-after="${BENCH_KILL_GRACE:-5}s" \
              "$((BENCH_RUNTIME + BENCH_WARMUP + BENCH_IO_TIMEOUT + BENCH_KILL_GRACE + 5))s" \
              sudo -n "$bench" --endpoints="$endpoint" --rw="$rw" --bs="$block_size" \
              --iodepth="$depth" --runtime="$BENCH_RUNTIME" \
              --ramp_time="$BENCH_WARMUP" --size="$TEST_BYTES" \
              --io_timeout_sec="${BENCH_IO_TIMEOUT:-30}" \
              --nof_workers=4 --nof_submit_chunk_bytes=131072 \
              --nof_inflight_bytes_limit=33554432 2>&1 | tee "$RESULT_DIR/nof/$name.log"
          else
            timeout --signal=TERM --kill-after="${BENCH_KILL_GRACE:-5}s" \
              "$((BENCH_RUNTIME + BENCH_WARMUP + BENCH_IO_TIMEOUT + BENCH_KILL_GRACE + 5))s" \
              sudo -n "$bench" --endpoints="$endpoint" --rw="$rw" --bs="$block_size" \
              --iodepth="$depth" --runtime="$BENCH_RUNTIME" \
              --ramp_time="$BENCH_WARMUP" --size="$TEST_BYTES" \
              --io_timeout_sec="${BENCH_IO_TIMEOUT:-30}" \
              --nof_workers=4 --nof_submit_chunk_bytes=131072 \
              --nof_inflight_bytes_limit=33554432 2>&1 | tee "$RESULT_DIR/nof/$name.log"
          fi
        done
      done
    done
  done
  for workers in 1 2 4; do
    for inflight in 8388608 33554432 134217728; do
      name="tune-workers$workers-inflight$inflight"
      sudo -n "$bench" --endpoints="$endpoint" --rw=read --bs=131072 \
        --iodepth="$BEST_IODEPTH" --runtime="$BENCH_RUNTIME" \
        --ramp_time="$BENCH_WARMUP" --size="$TEST_BYTES" \
        --nof_workers="$workers" --nof_submit_chunk_bytes=131072 \
        --nof_inflight_bytes_limit="$inflight" 2>&1 | tee "$RESULT_DIR/nof/$name.log"
    done
  done
  (rdma statistic show || true) >"$RESULT_DIR/telemetry/rdma-after.txt" 2>&1
  target "'$SPDK_DIR/scripts/rpc.py' bdev_nvme_get_controller_health_info 2>/dev/null || true" \
    >"$RESULT_DIR/telemetry/spdk-health-after.json"
}

run_characterization() {
  need fio
  need timeout
  [[ -n ${LOCAL_NVME_DEVICE:-} ]] || die "LOCAL_NVME_DEVICE is required"
  [[ -n ${LOCAL_NVME_SERIAL:-} ]] || die "LOCAL_NVME_SERIAL is required"
  [[ -n ${CLIENT_NET_INTERFACE:-} ]] || die "CLIENT_NET_INTERFACE is required"
  local bench="$BUILD_DIR/mooncake-store/benchmarks/nof_worker_pool_bench"
  [[ -x $bench ]] || die "benchmark binary missing: $bench"
  local actual_serial
  actual_serial=$(sudo -n nvme id-ctrl "$LOCAL_NVME_DEVICE" | awk -F: '/^sn[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
  [[ $actual_serial == "$LOCAL_NVME_SERIAL" ]] ||
    die "local NVMe serial mismatch: expected $LOCAL_NVME_SERIAL, got $actual_serial"
  device_is_read_safe "$LOCAL_NVME_DEVICE"

  local out="$RESULT_DIR/characterization"
  mkdir -p "$out/raw/local" "$out/raw/remote" "$out/raw/load" "$out/telemetry"
  python3 - "$out/matrix.json" <<EOF
import json, sys
json.dump({
    "sizes": "${CHAR_SIZES:-4096 65536 262144 1048576 4194304 16777216 67108864 268435456 1073741824}".split(),
    "local_loads": "${CHAR_LOADS:-0 25 50 75 90}".split(),
    "remote_loads": "${CHAR_REMOTE_LOADS:-0 25 50 75 90}".split(),
}, open(sys.argv[1], "w"), indent=2)
EOF
  local calibration="$out/raw/load/local-calibration.json"
  sudo -n fio --name=local-calibration --filename="$LOCAL_NVME_DEVICE" \
    --rw=randread --bs=4096 --iodepth="${CHAR_IODEPTH:-32}" --ioengine=libaio \
    --direct=1 --time_based=1 --runtime="$BENCH_RUNTIME" --ramp_time="$BENCH_WARMUP" \
    --size="$TEST_BYTES" --group_reporting=1 --output-format=json+ --output="$calibration"
  local calibration_iops
  calibration_iops=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(int(d["jobs"][0]["read"]["iops"]))' "$calibration")
  [[ $calibration_iops -gt 0 ]] || die "local calibration produced zero IOPS"

  local size load run name load_pid load_iops status timeout_sec depth inflight
  local remote_load_pid=""
  cleanup_characterization_loads() {
    if [[ -n ${load_pid:-} ]]; then
      sudo -n kill "$load_pid" 2>/dev/null || true
      wait "$load_pid" 2>/dev/null || true
    fi
    if [[ -n ${remote_load_pid:-} ]]; then
      sudo -n kill "$remote_load_pid" 2>/dev/null || true
      wait "$remote_load_pid" 2>/dev/null || true
    fi
  }
  trap cleanup_characterization_loads EXIT INT TERM
  for load in ${CHAR_LOADS:-0 25 50 75 90}; do
    load_pid=""
    if [[ $load -gt 0 ]]; then
      load_iops=$((calibration_iops * load / 100))
      sudo -n fio --name="local-load-$load" --filename="$LOCAL_NVME_DEVICE" \
        --rw=randread --bs=4096 --iodepth="${CHAR_IODEPTH:-32}" --ioengine=libaio \
        --direct=1 --time_based=1 --runtime=86400 \
        --rate_iops="$load_iops" --size="$TEST_BYTES" --group_reporting=1 \
        --output-format=json+ --output="$out/raw/load/local-load-$load.json" &
      load_pid=$!
      sleep 2
      sudo -n kill -0 "$load_pid" 2>/dev/null ||
        die "local background load failed to start: $out/raw/load/local-load-$load.json"
    fi
    for size in ${CHAR_SIZES:-4096 65536 262144 1048576 4194304 16777216 67108864 268435456 1073741824}; do
      depth=$((1073741824 / size))
      [[ $depth -gt $CHAR_IODEPTH ]] && depth=$CHAR_IODEPTH
      [[ $depth -gt 0 ]] || depth=1
      for run in $(seq 1 "$REPETITIONS"); do
        name="local-size${size}-load${load}-run${run}"
        python3 "$(dirname "$0")/telemetry.py" \
          --output "$out/telemetry/$name.system.json" \
          --interface "$CLIENT_NET_INTERFACE" --device "$LOCAL_NVME_DEVICE" -- \
          sudo -n fio --name="$name" --filename="$LOCAL_NVME_DEVICE" --rw=read \
          --bs="$size" --iodepth="$depth" --ioengine=libaio \
          --direct=1 --time_based=1 --runtime="$BENCH_RUNTIME" --ramp_time="$BENCH_WARMUP" \
          --size="$TEST_BYTES" --group_reporting=1 --output-format=json+ \
          --output="$out/raw/local/$name.json"
      done
    done
    if [[ -n $load_pid ]]; then
      sudo -n kill "$load_pid" 2>/dev/null || true
      wait "$load_pid" 2>/dev/null || true
    fi
  done

  timeout_sec=$((BENCH_RUNTIME + BENCH_WARMUP + BENCH_IO_TIMEOUT + BENCH_KILL_GRACE + 5))
  local remote_load_depth remote_load_bs
  remote_load_bs=${CHAR_REMOTE_LOAD_BS:-4096}
  for load in ${CHAR_REMOTE_LOADS:-0 25 50 75 90}; do
    remote_load_pid=""
    if [[ $load -gt 0 ]]; then
      remote_load_depth=$((CHAR_IODEPTH * load / 100))
      [[ $remote_load_depth -gt 0 ]] || remote_load_depth=1
      timeout --signal=TERM --kill-after="${BENCH_KILL_GRACE:-5}s" \
        86400s sudo -n "$bench" --endpoints="$endpoint" \
        --rw=randread --bs="$remote_load_bs" --iodepth="$remote_load_depth" \
        --runtime=86400 --ramp_time=0 --size="$TEST_BYTES" \
        --io_timeout_sec="${BENCH_IO_TIMEOUT:-30}" \
        >"$out/raw/load/remote-load-$load.log" 2>&1 &
      remote_load_pid=$!
      sleep 2
      sudo -n kill -0 "$remote_load_pid" 2>/dev/null ||
        die "remote background load failed to start: $out/raw/load/remote-load-$load.log"
    fi
    for size in ${CHAR_SIZES:-4096 65536 262144 1048576 4194304 16777216 67108864 268435456 1073741824}; do
      depth=$((1073741824 / size))
      [[ $depth -gt $CHAR_IODEPTH ]] && depth=$CHAR_IODEPTH
      [[ $depth -gt 0 ]] || depth=1
      inflight=$((size * depth))
      [[ $inflight -lt 134217728 ]] && inflight=134217728
      for run in $(seq 1 "$REPETITIONS"); do
        name="remote-size${size}-load${load}-run${run}"
        target "'$SPDK_DIR/scripts/rpc.py' bdev_get_iostat" \
          >"$out/telemetry/$name.spdk-before.json" 2>&1 || true
        set +e
        timeout --signal=TERM --kill-after="${BENCH_KILL_GRACE:-5}s" "${timeout_sec}s" \
          python3 "$(dirname "$0")/telemetry.py" \
          --output "$out/telemetry/$name.system.json" \
          --interface "$CLIENT_NET_INTERFACE" --device "$LOCAL_NVME_DEVICE" -- \
          /usr/bin/time -v -o "$out/telemetry/$name.time" \
          sudo -n "$bench" --endpoints="$endpoint" --rw=read --bs="$size" \
          --iodepth="$depth" --runtime="$BENCH_RUNTIME" --ramp_time="$BENCH_WARMUP" \
          --size="$TEST_BYTES" --io_timeout_sec="${BENCH_IO_TIMEOUT:-30}" \
          --nof_workers=4 --nof_submit_chunk_bytes=131072 \
          --nof_inflight_bytes_limit="$inflight" \
          >"$out/raw/remote/$name.log" 2>&1
        status=$?
        set -e
        target "'$SPDK_DIR/scripts/rpc.py' bdev_get_iostat" \
          >"$out/telemetry/$name.spdk-after.json" 2>&1 || true
        printf '%s\n' "$status" >"$out/raw/remote/$name.exitcode"
        [[ $status -eq 0 ]] || log "remote case failed: $name (exit $status)"
      done
    done
    if [[ -n $remote_load_pid ]]; then
      sudo -n kill "$remote_load_pid" 2>/dev/null || true
      wait "$remote_load_pid" 2>/dev/null || true
    fi
  done
  python3 "$(dirname "$0")/characterize.py" summarize "$out"
  python3 "$(dirname "$0")/characterize.py" plot "$out/summary.csv" "$out"
  trap - EXIT INT TERM
}

same_ssd_remote_run() {
  local phase=$1 out=$2 size=$3 depth=$4 run=$5
  local bench="${SAME_SSD_CLIENT_BUILD_DIR:-$BUILD_DIR}/mooncake-store/benchmarks/nof_worker_pool_bench"
  local name="$phase-size${size}-qd${depth}-run${run}"
  local status=0 timeout_sec
  timeout_sec=$((SAME_SSD_WARMUP + SAME_SSD_RUNTIME + BENCH_IO_TIMEOUT + BENCH_KILL_GRACE + 5))
  set +e
  if [[ -n ${SAME_SSD_CLIENT_SSH:-} ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$SAME_SSD_CLIENT_SSH" \
      "timeout --signal=TERM --kill-after='${BENCH_KILL_GRACE}s' '${timeout_sec}s' sudo -n taskset -c '$SAME_SSD_CLIENT_CPUS' '$bench' --endpoints='$endpoint' --rw=read --bs='$size' --iodepth='$depth' --runtime='$SAME_SSD_RUNTIME' --ramp_time='$SAME_SSD_WARMUP' --size='$TEST_BYTES' --io_timeout_sec='$BENCH_IO_TIMEOUT' --nof_workers=4 --nof_submit_chunk_bytes=131072 --nof_inflight_bytes_limit='$SAME_SSD_INFLIGHT_BYTES'" \
      >"$out/raw/$phase/$name.log" 2>&1
  else
    timeout --signal=TERM --kill-after="${BENCH_KILL_GRACE}s" "${timeout_sec}s" \
      sudo -n taskset -c "$SAME_SSD_CLIENT_CPUS" "$bench" \
      --endpoints="$endpoint" --rw=read --bs="$size" \
      --iodepth="$depth" --runtime="$SAME_SSD_RUNTIME" \
      --ramp_time="$SAME_SSD_WARMUP" --size="$TEST_BYTES" \
      --io_timeout_sec="$BENCH_IO_TIMEOUT" --nof_workers=4 \
      --nof_submit_chunk_bytes=131072 \
      --nof_inflight_bytes_limit="$SAME_SSD_INFLIGHT_BYTES" \
      >"$out/raw/$phase/$name.log" 2>&1
  fi
  status=$?
  set -e
  printf '%s\n' "$status" >"$out/raw/$phase/$name.exitcode"
  [[ $status -eq 0 ]] || log "same-SSD remote case failed: $name (exit $status)"
}

same_ssd_verify_listener() {
  target "'$SPDK_DIR/scripts/rpc.py' nvmf_get_subsystems" | python3 -c '
import json, sys
nqn, address, port, nsid = sys.argv[1:]
for subsystem in json.load(sys.stdin):
    if subsystem.get("nqn") != nqn:
        continue
    namespaces = {str(item.get("nsid")) for item in subsystem.get("namespaces", [])}
    listeners = subsystem.get("listen_addresses", [])
    if nsid in namespaces and any(
        item.get("trtype", "").upper() == "RDMA"
        and item.get("traddr") == address
        and str(item.get("trsvcid")) == port for item in listeners
    ):
        raise SystemExit(0)
raise SystemExit(1)
' "$NQN" "$TARGET_ADDR" "$TRSVCID" "$NSID"
}

same_ssd_probe() {
  local output=$1
  SAME_SSD_WARMUP=0 SAME_SSD_RUNTIME=1 same_ssd_remote_run \
    capability "$output" 4096 1 0
  [[ $(<"$output/raw/capability/capability-size4096-qd1-run0.exitcode") == 0 ]]
}

same_ssd_start_target() {
  local rpc="$SPDK_DIR/scripts/rpc.py"
  target "sudo -n systemd-run --unit='${SAME_SSD_SERVICE%.service}' --property=LimitMEMLOCK=infinity '$SPDK_DIR/build/bin/nvmf_tgt' -m '$SAME_SSD_TARGET_CPU_MASK' --wait-for-rpc"
  local attempt ready=false
  for attempt in $(seq 1 30); do
    if target "'$rpc' rpc_get_methods >/dev/null 2>&1"; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ $ready == true ]] || die "SPDK RPC socket did not become ready"
  target "'$rpc' framework_start_init"
  target "'$rpc' nvmf_create_transport -t RDMA -q 128 -m 127 -c 131072 -u 131072 -n 4096 -b 32 -s '$SAME_SSD_MAX_SRQ_DEPTH'"
  target "'$rpc' bdev_nvme_attach_controller -b Nvme0 -t PCIe -a '$TARGET_NVME_BDF'"
  target "'$rpc' nvmf_create_subsystem '$NQN' -a -s MOONCAKE01 -d 'Mooncake NVMe-oF validation'"
  target "'$rpc' nvmf_subsystem_add_ns '$NQN' Nvme0n1 -n '$NSID'"
  target "'$rpc' nvmf_subsystem_add_listener '$NQN' -t RDMA -a '$TARGET_ADDR' -s '$TRSVCID' -f ipv4"
}

same_ssd_preflight() {
  need ssh; need timeout; need python3
  [[ -n ${TARGET_SSH:-} ]] || die "TARGET_SSH is required"
  [[ -n ${TARGET_NVME_BDF:-} && -n ${TARGET_NVME_SERIAL:-} ]] ||
    die "TARGET_NVME_BDF and TARGET_NVME_SERIAL are required"
  local client_build_dir="${SAME_SSD_CLIENT_BUILD_DIR:-$BUILD_DIR}"
  if [[ -n ${SAME_SSD_CLIENT_SSH:-} ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$SAME_SSD_CLIENT_SSH" \
      "test -x '$client_build_dir/mooncake-store/benchmarks/nof_worker_pool_bench' && sudo -n true"
  else
    [[ -x "$client_build_dir/mooncake-store/benchmarks/nof_worker_pool_bench" ]] ||
      die "NoF benchmark binary is missing"
  fi
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SSH" true
  target "sudo -n true"
  target "test -d '/sys/bus/pci/devices/$TARGET_NVME_BDF'"
  target "test -x '$SPDK_DIR/scripts/rpc.py'"
  target "systemctl is-active --quiet '$SAME_SSD_SERVICE'"
  verify_spdk_target_serial
  same_ssd_verify_listener
  if [[ -n ${SAME_SSD_CLIENT_SSH:-} ]]; then
    ssh "$SAME_SSD_CLIENT_SSH" "rdma link show | grep -F '$CLIENT_RDMA_DEVICE' >/dev/null" ||
      die "client RDMA device is unavailable: $CLIENT_RDMA_DEVICE"
  else
    rdma link show | grep -F "$CLIENT_RDMA_DEVICE" >/dev/null ||
      die "client RDMA device is unavailable: $CLIENT_RDMA_DEVICE"
  fi
}

run_same_ssd_characterization() {
  same_ssd_preflight
  local stamp out bdevperf config size depth run status service_stopped=0
  local split_size_mb=$((TEST_BYTES / 1048576))
  [[ $split_size_mb -gt 0 && $((split_size_mb * 1048576)) -eq TEST_BYTES ]] ||
    die "TEST_BYTES must be a positive whole number of MiB"
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  out="${SAME_SSD_RESULT_DIR:-$RESULT_DIR/same-ssd-$stamp}"
  bdevperf="$SPDK_DIR/build/examples/bdevperf"
  mkdir -p "$out"/{raw/remote-before,raw/local,raw/remote-after,raw/capability,environment,smart}
  python3 - "$out/matrix.json" <<EOF
import json, sys
json.dump({"sizes": "${SAME_SSD_SIZES}".split(), "depths": "${SAME_SSD_DEPTHS}".split(),
           "repetitions": int("${SAME_SSD_REPETITIONS}"), "warmup_sec": int("${SAME_SSD_WARMUP}"),
           "runtime_sec": int("${SAME_SSD_RUNTIME}"), "capability_probe_bytes": 67108864},
          open(sys.argv[1], "w"), indent=2)
EOF
  if [[ -n ${SAME_SSD_CLIENT_SSH:-} ]]; then
    ssh "$SAME_SSD_CLIENT_SSH" \
      "git -C '${SAME_SSD_CLIENT_ROOT:-$MOONCAKE_ROOT}' rev-parse HEAD" \
      >"$out/environment/mooncake-commit.txt"
  else
    git -C "$MOONCAKE_ROOT" rev-parse HEAD >"$out/environment/mooncake-commit.txt"
  fi
  target "cd '$SPDK_DIR' && git describe --always --dirty" >"$out/environment/spdk-version.txt"
  target "systemctl status --no-pager '$SAME_SSD_SERVICE'" >"$out/environment/service-before.txt"
  target "lspci -Dnn -s '$TARGET_NVME_BDF'" >"$out/environment/target-pci.txt"
  capture_target_smart "$out/smart/before.json"
  if ! target "test -x '$bdevperf'"; then
    target "cd '$SPDK_DIR' && ninja -C build bdevperf" ||
      target "cd '$SPDK_DIR' && make -j\$(nproc) build/examples/bdevperf"
  fi
  for size in $SAME_SSD_SIZES; do
    for depth in $SAME_SSD_DEPTHS; do
      for run in $(seq 1 "$SAME_SSD_REPETITIONS"); do
        same_ssd_remote_run remote-before "$out" "$size" "$depth" "$run"
      done
    done
  done
  restore_same_ssd_service() {
    local recovered=false
    if [[ $service_stopped -eq 1 ]]; then
      target "sudo -n pkill -TERM -f '$bdevperf.*$config' 2>/dev/null || true"
      target "rm -f '$config'"
      if same_ssd_start_target &&
        target "systemctl is-active --quiet '$SAME_SSD_SERVICE'" &&
        verify_spdk_target_serial && same_ssd_verify_listener && same_ssd_probe "$out"; then
        recovered=true
        capture_target_smart "$out/smart/after.json"
        service_stopped=0
      fi
    fi
    printf '{"success": %s}\n' "$recovered" >"$out/recovery.json"
    [[ $recovered == true ]]
  }
  trap 'restore_same_ssd_service || true' EXIT
  trap 'restore_same_ssd_service || true; trap - EXIT; exit 130' INT TERM
  target "sudo -n systemctl stop '$SAME_SSD_SERVICE'"
  service_stopped=1
  target "! systemctl is-active --quiet '$SAME_SSD_SERVICE'"
  config="/tmp/mooncake-same-ssd-$stamp.json"
  target "printf '%s\n' '{\"subsystems\":[{\"subsystem\":\"bdev\",\"config\":[{\"method\":\"bdev_nvme_attach_controller\",\"params\":{\"name\":\"Nvme0\",\"trtype\":\"PCIe\",\"traddr\":\"$TARGET_NVME_BDF\"}},{\"method\":\"bdev_split_create\",\"params\":{\"base_bdev\":\"Nvme0n1\",\"split_count\":1,\"split_size_mb\":$split_size_mb}}]}]}' >'$config'"
  for size in $SAME_SSD_SIZES; do
    for depth in $SAME_SSD_DEPTHS; do
      for run in $(seq 1 "$SAME_SSD_REPETITIONS"); do
        name="local-size${size}-qd${depth}-run${run}"
        set +e
        target "'$bdevperf' -c '$config' -m '$SAME_SSD_TARGET_CPU_MASK' -T Nvme0n1p0 -q '$depth' -o '$size' -w read -t '$SAME_SSD_WARMUP' >/dev/null && '$bdevperf' -c '$config' -m '$SAME_SSD_TARGET_CPU_MASK' -T Nvme0n1p0 -q '$depth' -o '$size' -w read -t '$SAME_SSD_RUNTIME'" \
          >"$out/raw/local/$name.log" 2>&1
        status=$?
        set -e
        printf '%s\n' "$status" >"$out/raw/local/$name.exitcode"
      done
    done
  done
  target "rm -f '$config'"
  restore_same_ssd_service || die "NVMe-oF target recovery or remote probe failed"
  trap - EXIT INT TERM
  for size in $SAME_SSD_SIZES; do
    for depth in $SAME_SSD_DEPTHS; do
      for run in $(seq 1 "$SAME_SSD_REPETITIONS"); do
        same_ssd_remote_run remote-after "$out" "$size" "$depth" "$run"
      done
    done
  done
  same_ssd_remote_run capability "$out" 67108864 1 1
  status=$(<"$out/raw/capability/capability-size67108864-qd1-run1.exitcode")
  printf '{"size_bytes":67108864,"exit_code":%s,"required":false}\n' "$status" \
    >"$out/capability-probe.json"
  target "systemctl status --no-pager '$SAME_SSD_SERVICE'" >"$out/environment/service-after.txt"
  python3 "$(dirname "$0")/same_ssd.py" summarize "$out"
  log "same-SSD results: $out"
}

export TARGET_ADDR TARGET_NVME_SERIAL CLIENT_RDMA_DEVICE LOCAL_HOSTNAME
export METADATA_URL MASTER_ADDR GLOBAL_SEGMENT_SIZE LOCAL_BUFFER_SIZE
export NQN NSID TRSVCID TEST_BYTES
case ${1:-help} in
  inventory)
    mkdir -p "$RESULT_DIR/inventory"
    git -C "$MOONCAKE_ROOT" rev-parse HEAD >"$RESULT_DIR/inventory/mooncake-commit.txt"
    inventory_one "$RESULT_DIR/inventory/client.txt" env
    inventory_one "$RESULT_DIR/inventory/target.txt" ssh -o BatchMode=yes "$TARGET_SSH"
    target "cd '$SPDK_DIR' && git describe --always --dirty 2>/dev/null || build/bin/nvmf_tgt --version" >"$RESULT_DIR/inventory/spdk-version.txt"
    ;;
  preflight)
    need nvme; need ssh; need ping
    ping -c 3 "$TARGET_ADDR"
    device_is_safe "$CLIENT_DEVICE"
    verify_serial
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      verify_spdk_target_serial
    else
      target_device_is_safe
      verify_target_serial
    fi
    mkdir -p "$RESULT_DIR/smart"
    sudo -n nvme smart-log "$CLIENT_DEVICE" | tee "$RESULT_DIR/smart/client-before.txt"
    sudo -n nvme smart-log -o json "$CLIENT_DEVICE" >"$RESULT_DIR/smart/client-before.json"
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      capture_target_smart "$RESULT_DIR/smart/target-before.json"
      python3 -m json.tool "$RESULT_DIR/smart/target-before.json" | tee "$RESULT_DIR/smart/target-before.txt"
    else
      target "nvme smart-log '$TARGET_DEVICE'" | tee "$RESULT_DIR/smart/target-before.txt"
      target "nvme smart-log -o json '$TARGET_DEVICE'" >"$RESULT_DIR/smart/target-before.json"
    fi
    smart_json_is_safe "$RESULT_DIR/smart/client-before.json"
    smart_json_is_safe "$RESULT_DIR/smart/target-before.json"
    ;;
  baseline) run_fio_matrix ;;
  disconnect-kernel)
    device_is_safe "$CLIENT_DEVICE"
    subsystem=$(nvme list-subsys "$CLIENT_DEVICE" | awk '/^nvme-subsys/ {sub(/^.*NQN=/, ""); print; exit}')
    [[ -n $subsystem ]] || die "could not resolve NQN for $CLIENT_DEVICE"
    sudo -n nvme disconnect -n "$subsystem"
    ! nvme list-subsys | grep -Fq "NQN=$subsystem" ||
      die "kernel initiator remains connected to $subsystem"
    ;;
  target-start|target-status|target-stop|target-cleanup) "$(dirname "$0")/target.sh" "${1#target-}" ;;
  register)
    run_store_python -c 'import os; from mooncake.store import MooncakeDistributedNoFRegister as R; rc=R().real_register(os.environ["NQN"], int(os.environ["NSID"]), os.environ["TARGET_ADDR"], int(os.environ["TRSVCID"]), 0, int(os.environ["TEST_BYTES"]), os.environ["MASTER_ADDR"]); raise SystemExit(rc)'
    ;;
  unregister)
    run_store_python -c 'import os; from mooncake.store import MooncakeDistributedNoFRegister as R; rc=R().real_unregister_by_endpoint(os.environ["NQN"], int(os.environ["NSID"]), os.environ["TARGET_ADDR"], int(os.environ["TRSVCID"]), os.environ["MASTER_ADDR"]); raise SystemExit(rc)'
    ;;
  service-commands)
    printf '%s\n' \
      "sudo -n $BUILD_DIR/mooncake-store/src/mooncake_master --rpc_address=$CLIENT_ADDR 2>&1 | tee $RESULT_DIR/mooncake-master.log" \
      "python3 -m mooncake.http_metadata_server --host=$CLIENT_ADDR --port=8080 2>&1 | tee $RESULT_DIR/metadata.log"
    ;;
  correctness) run_store_python "$(dirname "$0")/correctness.py" run --output "$RESULT_DIR/correctness.json" ;;
  transparent-local) require_transparent_run_id; ENABLE_SSD_OFFLOAD=1 run_store_python "$(dirname "$0")/correctness.py" transparent --expected-targets local_nvme --output "$RESULT_DIR/transparent-local.json" ;;
  transparent-remote) require_transparent_run_id; run_store_python "$(dirname "$0")/correctness.py" transparent --expected-targets remote_nof --output "$RESULT_DIR/transparent-remote.json" ;;
  transparent-round-robin) require_transparent_run_id; ENABLE_SSD_OFFLOAD=1 run_store_python "$(dirname "$0")/correctness.py" transparent --expected-targets local_nvme,remote_nof --output "$RESULT_DIR/transparent-round-robin.json" ;;
  transparent-local-unavailable) require_transparent_run_id; run_store_python "$(dirname "$0")/correctness.py" transparent-unavailable --expected-target local_nvme --output "$RESULT_DIR/transparent-local-unavailable.json" ;;
  transparent-remote-unavailable) require_transparent_run_id; run_store_python "$(dirname "$0")/correctness.py" transparent-unavailable --expected-target remote_nof --output "$RESULT_DIR/transparent-remote-unavailable.json" ;;
  transparent-restart-seed)
    require_transparent_run_id
    : "${TRANSPARENT_RESTART_SCENARIO:?set TRANSPARENT_RESTART_SCENARIO}"
    : "${TRANSPARENT_RESTART_TARGETS:?set TRANSPARENT_RESTART_TARGETS}"
    restart_witness_args=()
    if [[ $TRANSPARENT_RESTART_SCENARIO != client_restart ]]; then
      : "${TRANSPARENT_RESTART_WITNESS:?set TRANSPARENT_RESTART_WITNESS to the current service incarnation identity}"
      restart_witness_args=(--witness "$TRANSPARENT_RESTART_WITNESS")
    fi
    [[ $TRANSPARENT_RESTART_TARGETS != *local_nvme* ]] || ENABLE_SSD_OFFLOAD=1
    run_store_python "$(dirname "$0")/correctness.py" transparent-restart-seed --scenario "$TRANSPARENT_RESTART_SCENARIO" --expected-targets "$TRANSPARENT_RESTART_TARGETS" --count "${TRANSPARENT_RESTART_COUNT:-12}" "${restart_witness_args[@]}" --output "$RESULT_DIR/transparent-restart-seed-$TRANSPARENT_RESTART_SCENARIO.json"
    ;;
  transparent-restart-verify)
    require_transparent_run_id
    : "${TRANSPARENT_RESTART_SCENARIO:?set TRANSPARENT_RESTART_SCENARIO}"
    restart_witness_args=()
    if [[ $TRANSPARENT_RESTART_SCENARIO != client_restart ]]; then
      : "${TRANSPARENT_RESTART_WITNESS:?set TRANSPARENT_RESTART_WITNESS to the new service incarnation identity}"
      restart_witness_args=(--witness "$TRANSPARENT_RESTART_WITNESS")
    fi
    [[ -f $RESULT_DIR/transparent-restart-seed-$TRANSPARENT_RESTART_SCENARIO.json ]] || die "restart seed manifest is missing"
    ! grep -Fq '"local_nvme"' "$RESULT_DIR/transparent-restart-seed-$TRANSPARENT_RESTART_SCENARIO.json" || ENABLE_SSD_OFFLOAD=1
    run_store_python "$(dirname "$0")/correctness.py" transparent-restart-verify --manifest "$RESULT_DIR/transparent-restart-seed-$TRANSPARENT_RESTART_SCENARIO.json" "${restart_witness_args[@]}" --output "$RESULT_DIR/transparent-restart-$TRANSPARENT_RESTART_SCENARIO.json"
    ;;
  transparent-benchmark)
    require_transparent_run_id
    [[ ${TRANSPARENT_BENCH_TARGET:?set TRANSPARENT_BENCH_TARGET=local_nvme or remote_nof} != local_nvme ]] || ENABLE_SSD_OFFLOAD=1
    run_store_python "$(dirname "$0")/correctness.py" transparent-benchmark --mode "${TRANSPARENT_BENCH_MODE:-transparent}" --target "$TRANSPARENT_BENCH_TARGET" --count "${TRANSPARENT_BENCH_COUNT:-100}" --size "${TRANSPARENT_BENCH_SIZE:-131072}" --output "$RESULT_DIR/transparent-benchmark-${TRANSPARENT_BENCH_MODE:-transparent}-${TRANSPARENT_BENCH_TARGET}.json"
    ;;
  transparent-overhead)
    require_transparent_run_id
    [[ ${TRANSPARENT_BENCH_TARGET:?set TRANSPARENT_BENCH_TARGET=local_nvme or remote_nof} != local_nvme ]] || ENABLE_SSD_OFFLOAD=1
    run_store_python "$(dirname "$0")/correctness.py" transparent-overhead --target "$TRANSPARENT_BENCH_TARGET" --count "${TRANSPARENT_BENCH_COUNT:-100}" --size "${TRANSPARENT_BENCH_SIZE:-131072}" --output "$RESULT_DIR/transparent-overhead-$TRANSPARENT_BENCH_TARGET.json"
    ;;
  transparent-software-verification)
    require_transparent_run_id
    python3 "$(dirname "$0")/correctness.py" transparent-software-verification --repo-root "$(cd "$(dirname "$0")/../.." && pwd)" --build-dir "$BUILD_DIR" --output "$RESULT_DIR/transparent-software-verification.json"
    ;;
  transparent-acceptance)
    require_transparent_run_id
    python3 "$(dirname "$0")/correctness.py" transparent-acceptance --result-dir "$RESULT_DIR" --run-id "$TRANSPARENT_RUN_ID" --output "$RESULT_DIR/transparent-acceptance.json"
    ;;
  stability) run_store_python "$(dirname "$0")/correctness.py" stability --seconds "${STABILITY_SECONDS:-60}" --output "$RESULT_DIR/stability.json" ;;
  nof-benchmark) run_nof_matrix ;;
  characterize) run_characterization ;;
  same-ssd-preflight) same_ssd_preflight ;;
  same-ssd-characterize) run_same_ssd_characterization ;;
  same-ssd-summarize)
    [[ -n ${SAME_SSD_RESULT_DIR:-} ]] || die "set SAME_SSD_RESULT_DIR to a same-SSD result directory"
    python3 "$(dirname "$0")/same_ssd.py" summarize "$SAME_SSD_RESULT_DIR"
    ;;
  kv-workload-generate) kv_workload_generate ;;
  kv-workload-replay) kv_workload_replay ;;
  kv-workload-summarize) kv_workload_summarize ;;
  characterize-summarize)
    python3 "$(dirname "$0")/characterize.py" summarize "$RESULT_DIR/characterization"
    python3 "$(dirname "$0")/characterize.py" plot "$RESULT_DIR/characterization/summary.csv" "$RESULT_DIR/characterization"
    ;;
  summarize) python3 "$(dirname "$0")/summarize.py" "$RESULT_DIR" ;;
  post-smart)
    mkdir -p "$RESULT_DIR/smart"
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      capture_target_smart "$RESULT_DIR/smart/target-after.json"
      python3 -m json.tool "$RESULT_DIR/smart/target-after.json" | tee "$RESULT_DIR/smart/target-after.txt"
    else
      target "nvme smart-log '$TARGET_DEVICE'" | tee "$RESULT_DIR/smart/target-after.txt"
      target "nvme smart-log -o json '$TARGET_DEVICE'" >"$RESULT_DIR/smart/target-after.json"
    fi
    ;;
  *) sed -n '1,180p' "$(dirname "$0")/README.md" ;;
esac
