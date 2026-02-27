#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <device_path> <image_path>" >&2
  exit 1
}

[[ $# -eq 2 ]] || usage

DEVICE_PATH="$1"
IMAGE_PATH="$2"
BLOCK_DEVICE_PATH="${DEVICE_PATH}"
OS_NAME="$(uname -s)"

platform_unmount_device_before_write() {
  local dev="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    diskutil unmountDisk force "${dev}" >/dev/null 2>&1 || true
    return 0
  fi
  if [[ "${OS_NAME}" == "Linux" ]]; then
    if ! command -v lsblk >/dev/null 2>&1; then
      return 0
    fi
    while IFS= read -r mountpoint; do
      [[ -n "${mountpoint}" ]] || continue
      umount "${mountpoint}" >/dev/null 2>&1 || true
    done < <(lsblk -ln -o MOUNTPOINT "${dev}" 2>/dev/null | awk 'NF {print $0}' | sort -r)
    return 0
  fi
}

platform_run_dd_with_progress() {
  local image_path="$1"
  local dev="$2"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    dd if="${image_path}" of="${dev}" bs=4m status=progress &
    local dd_pid=$!
    while kill -0 "${dd_pid}" >/dev/null 2>&1; do
      kill -INFO "${dd_pid}" >/dev/null 2>&1 || true
      sleep 1
    done
    wait "${dd_pid}"
    return $?
  fi
  dd if="${image_path}" of="${dev}" bs=4M status=progress
}

platform_unmount_device_before_write "${BLOCK_DEVICE_PATH}"

IMAGE_SIZE_BYTES="$(wc -c < "${IMAGE_PATH}")"
IMAGE_SIZE_BYTES="${IMAGE_SIZE_BYTES//[[:space:]]/}"

[[ "${IMAGE_SIZE_BYTES}" =~ ^[0-9]+$ ]] && [[ "${IMAGE_SIZE_BYTES}" -gt 0 ]] || {
  echo "Failed to determine image size: ${IMAGE_PATH}" >&2
  exit 1
}

echo "FLASH_IMAGE_SIZE_BYTES=${IMAGE_SIZE_BYTES}"
platform_run_dd_with_progress "${IMAGE_PATH}" "${BLOCK_DEVICE_PATH}"
sync

if [[ "${OS_NAME}" != "Darwin" ]]; then
  if ! cmp -n "${IMAGE_SIZE_BYTES}" "${IMAGE_PATH}" "${BLOCK_DEVICE_PATH}" >/dev/null; then
    echo "Verification failed: image and device differ (cmp mismatch)." >&2
    exit 1
  fi
fi

echo "OK"
