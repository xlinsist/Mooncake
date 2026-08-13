#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE=${CONFIG_FILE:-"$SCRIPT_DIR/config.env"}
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Missing $CONFIG_FILE; copy config.env.example and edit it." >&2
  exit 2
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

RESULT_ROOT=${RESULT_ROOT:-"$SCRIPT_DIR/results"}
if [[ -n ${RESULT_DIR:-} ]]; then
  mkdir -p "$RESULT_DIR"
elif [[ -f "$RESULT_ROOT/latest" ]]; then
  RESULT_DIR=$(<"$RESULT_ROOT/latest")
else
  RESULT_DIR="$RESULT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$RESULT_ROOT"
  printf '%s\n' "$RESULT_DIR" >"$RESULT_ROOT/latest"
fi
mkdir -p "$RESULT_DIR"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "required command not found: $1"; }
target() {
  local remote_command
  printf -v remote_command '%q' "$*"
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SSH" \
    "sudo -n bash -lc $remote_command"
}

require_destructive_confirmation() {
  local expected="ERASE-$TARGET_NVME_SERIAL"
  [[ ${TARGET_NVME_SERIAL:-REPLACE_ME} != REPLACE_ME ]] || die "set TARGET_NVME_SERIAL"
  [[ ${DESTRUCTIVE_CONFIRM:-} == "$expected" ]] ||
    die "set DESTRUCTIVE_CONFIRM=$expected after verifying the target serial"
}

device_is_safe() {
  local device=$1
  [[ -b "$device" ]] || die "block device does not exist: $device"
  lsblk -nr -o MOUNTPOINT "$device" | grep -q '[^[:space:]]' &&
    die "$device or a partition is mounted"
  lsblk -nr -o TYPE "$device" | tail -n +2 | grep -Eq 'lvm|raid|crypt' &&
    die "$device has LVM, RAID, or crypt holders"
  swapon --noheadings --raw --output NAME 2>/dev/null | grep -q "^$device" &&
    die "$device is used as swap"
  if command -v fuser >/dev/null && sudo -n fuser "$device" >/dev/null 2>&1; then
    die "$device is open by a process"
  fi
}

device_is_read_safe() {
  local device=$1
  [[ -b "$device" ]] || die "block device does not exist: $device"
  lsblk -nr -o TYPE "$device" | tail -n +2 | grep -Eq 'lvm|raid|crypt' &&
    die "$device has LVM, RAID, or crypt holders"
  swapon --noheadings --raw --output NAME 2>/dev/null | grep -q "^$device" &&
    die "$device is used as swap"
  if lsblk -nr -o MOUNTPOINT "$device" | grep -q '[^[:space:]]'; then
    log "warning: $device is mounted; characterization is read-only but filesystem traffic may affect results"
  fi
}

target_device_is_safe() {
  target "test -b '$TARGET_DEVICE'" || die "target device missing: $TARGET_DEVICE"
  target "lsblk -nr -o MOUNTPOINT '$TARGET_DEVICE' | grep -q '[^[:space:]]'" &&
    die "$TARGET_DEVICE or a target partition is mounted"
  target "lsblk -nr -o TYPE '$TARGET_DEVICE' | tail -n +2 | grep -Eq 'lvm|raid|crypt'" &&
    die "$TARGET_DEVICE has LVM, RAID, or crypt holders"
  target "swapon --noheadings --raw --output NAME 2>/dev/null | grep -q '^$TARGET_DEVICE'" &&
    die "$TARGET_DEVICE is used as swap"
  target "command -v fuser >/dev/null && fuser '$TARGET_DEVICE' >/dev/null 2>&1" &&
    die "$TARGET_DEVICE is open by a process"
}

verify_serial() {
  local actual
  actual=$(sudo -n nvme id-ctrl "$CLIENT_DEVICE" | awk -F: '/^sn[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
  [[ ${CLIENT_NVME_SERIAL:-REPLACE_ME} != REPLACE_ME ]] ||
    die "set CLIENT_NVME_SERIAL"
  [[ "$actual" == "$CLIENT_NVME_SERIAL" ]] ||
    die "client serial '$actual' does not match configured '$CLIENT_NVME_SERIAL'"
}

verify_target_serial() {
  local actual
  actual=$(target "nvme id-ctrl '$TARGET_DEVICE'" | awk -F: '/^sn[[:space:]]*:/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
  [[ "$actual" == "$TARGET_NVME_SERIAL" ]] ||
    die "target serial '$actual' does not match configured '$TARGET_NVME_SERIAL'"
}

verify_spdk_target_serial() {
  local actual
  actual=$(target "'$SPDK_DIR/scripts/rpc.py' bdev_get_bdevs" | python3 -c '
import json, sys
for bdev in json.load(sys.stdin):
    for controller in bdev.get("driver_specific", {}).get("nvme", []):
        if controller.get("pci_address") == sys.argv[1]:
            print(controller.get("ctrlr_data", {}).get("serial_number", "").strip())
            raise SystemExit
' "$TARGET_NVME_BDF")
  [[ "$actual" == "$TARGET_NVME_SERIAL" ]] ||
    die "SPDK target serial '$actual' does not match '$TARGET_NVME_SERIAL'"
}

capture_target_smart() {
  local output=$1
  local controller
  controller=$(target "'$SPDK_DIR/scripts/rpc.py' bdev_get_bdevs" | python3 -c '
import json, re, sys
for bdev in json.load(sys.stdin):
    for device in bdev.get("driver_specific", {}).get("nvme", []):
        if device.get("pci_address") == sys.argv[1]:
            print(re.sub(r"n[0-9]+$", "", bdev["name"]))
            raise SystemExit(0)
raise SystemExit(1)
' "$TARGET_NVME_BDF")
  [[ -n $controller ]] || die "could not resolve SPDK controller for $TARGET_NVME_BDF"
  target "'$SPDK_DIR/scripts/rpc.py' bdev_nvme_get_controller_health_info -c '$controller'" >"$output"
}

smart_json_is_safe() {
  local snapshot=$1
  python3 - "$snapshot" "$MAX_TEMP_C" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
maximum_temperature = float(sys.argv[2])
critical_warning = int(data.get("critical_warning", 0))
media_errors = int(data.get("media_errors", 0))
temperature = float(data.get("temperature_celsius", data.get("temperature", 0)))
if temperature > 200:
    temperature -= 273.15
if critical_warning or media_errors or temperature >= maximum_temperature:
    raise SystemExit(
        f"unsafe SMART: critical_warning={critical_warning}, "
        f"media_errors={media_errors}, temperature_c={temperature:.1f}"
    )
PY
}

endpoint="traddr:$TARGET_ADDR trsvcid:$TRSVCID subnqn:$NQN trtype:RDMA adrfam:IPv4 ns:$NSID"
