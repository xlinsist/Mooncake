#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

action=${1:-status}
rpc="$SPDK_DIR/scripts/rpc.py"
pidfile=/tmp/mooncake-nvmf-tgt.pid
logfile=/tmp/mooncake-nvmf-tgt.log

case "$action" in
  start)
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      [[ -n ${SPDK_SERVICE:-} ]] || die "SPDK_SERVICE is required"
      target "systemctl start '$SPDK_SERVICE'"
      verify_spdk_target_serial
      "$0" status
      exit 0
    fi
    require_destructive_confirmation
    target_device_is_safe
    target "test -x '$SPDK_DIR/build/bin/nvmf_tgt' && test -x '$rpc'"
    target "test -d '/sys/bus/pci/devices/$TARGET_NVME_BDF'"
    target "udevadm info -q path -n '$TARGET_DEVICE' | grep -q '/$TARGET_NVME_BDF/'" ||
      die "$TARGET_NVME_BDF does not own $TARGET_DEVICE"
    target "echo '$HUGEPAGES' > /proc/sys/vm/nr_hugepages"
    target "cd '$SPDK_DIR' && PCI_ALLOWED='$TARGET_NVME_BDF' scripts/setup.sh"
    target "nohup '$SPDK_DIR/build/bin/nvmf_tgt' -m 0xff >'$logfile' 2>&1 & echo \$! >'$pidfile'"
    for _ in $(seq 1 30); do
      target "'$rpc' rpc_get_methods >/dev/null 2>&1" && break
      sleep 1
    done
    target "'$rpc' nvmf_create_transport -t RDMA -q 128 -m 127 -c 131072 -u 131072 -n 4096 -b 32 -s '${SPDK_MAX_SRQ_DEPTH:-128}'"
    target "'$rpc' bdev_nvme_attach_controller -b Nvme0 -t PCIe -a '$TARGET_NVME_BDF'"
    target "'$rpc' nvmf_create_subsystem '$NQN' -a -s MOONCAKE01 -d 'Mooncake NVMe-oF validation'"
    target "'$rpc' nvmf_subsystem_add_ns '$NQN' Nvme0n1 -n '$NSID'"
    target "'$rpc' nvmf_subsystem_add_listener '$NQN' -t RDMA -a '$TARGET_ADDR' -s '$TRSVCID'"
    "$0" status
    ;;
  status)
    mkdir -p "$RESULT_DIR/target"
    target "'$rpc' bdev_get_bdevs" | tee "$RESULT_DIR/target/bdev_get_bdevs.json"
    target "'$rpc' nvmf_get_subsystems" | tee "$RESULT_DIR/target/nvmf_get_subsystems.json"
    ;;
  stop)
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      [[ -n ${SPDK_SERVICE:-} ]] || die "SPDK_SERVICE is required"
      target "systemctl stop '$SPDK_SERVICE'"
    else
      target "test ! -f '$pidfile' || kill \$(cat '$pidfile')"
    fi
    ;;
  cleanup)
    if [[ ${TARGET_PRECONFIGURED:-0} == 1 ]]; then
      log "preconfigured target retained; no binding or service cleanup performed"
      exit 0
    fi
    target "test ! -f '$pidfile' || kill \$(cat '$pidfile') 2>/dev/null || true; rm -f '$pidfile'"
    target "cd '$SPDK_DIR' && scripts/setup.sh reset"
    for _ in $(seq 1 30); do
      target "test -b '$TARGET_DEVICE'" && break
      sleep 1
    done
    actual=$(target "nvme id-ctrl '$TARGET_DEVICE'" | awk -F: '/^sn[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
    [[ "$actual" == "$TARGET_NVME_SERIAL" ]] || die "restored serial mismatch: $actual"
    ;;
  *) die "usage: $0 {start|status|stop|cleanup}" ;;
esac
