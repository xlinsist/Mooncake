#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

export PYTHONPATH="$BUILD_DIR/mooncake-integration${PYTHONPATH:+:$PYTHONPATH}"

run_store_python() {
  sudo -n env PYTHONPATH="$PYTHONPATH" \
    TARGET_ADDR="$TARGET_ADDR" TARGET_NVME_SERIAL="$TARGET_NVME_SERIAL" \
    CLIENT_RDMA_DEVICE="$CLIENT_RDMA_DEVICE" LOCAL_HOSTNAME="$LOCAL_HOSTNAME" \
    METADATA_URL="$METADATA_URL" MASTER_ADDR="$MASTER_ADDR" \
    GLOBAL_SEGMENT_SIZE="$GLOBAL_SEGMENT_SIZE" \
    LOCAL_BUFFER_SIZE="$LOCAL_BUFFER_SIZE" NQN="$NQN" NSID="$NSID" \
    TRSVCID="$TRSVCID" TEST_BYTES="$TEST_BYTES" python3 "$@"
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
              sudo -n "$bench" --endpoints="$endpoint" --rw="$rw" --bs="$block_size" \
              --iodepth="$depth" --runtime="$BENCH_RUNTIME" \
              --ramp_time="$BENCH_WARMUP" --size="$TEST_BYTES" \
              --nof_workers=4 --nof_submit_chunk_bytes=131072 \
              --nof_inflight_bytes_limit=33554432 2>&1 | tee "$RESULT_DIR/nof/$name.log"
          else
            sudo -n "$bench" --endpoints="$endpoint" --rw="$rw" --bs="$block_size" \
              --iodepth="$depth" --runtime="$BENCH_RUNTIME" \
              --ramp_time="$BENCH_WARMUP" --size="$TEST_BYTES" \
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
  stability) run_store_python "$(dirname "$0")/correctness.py" stability --seconds "${STABILITY_SECONDS:-60}" --output "$RESULT_DIR/stability.json" ;;
  nof-benchmark) run_nof_matrix ;;
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
