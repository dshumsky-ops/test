#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <base_image> <config_package>" >&2
  exit 1
}

[[ $# -eq 2 ]] || usage

BASE_IMAGE="$1"
CONFIG_PACKAGE="$2"

TMPDIR_WORK="$(mktemp -d "${TMPDIR:-/tmp}/imgcfg.XXXXXX")"
MOUNT_DIR="${TMPDIR_WORK}/mount"
CONFIG_BASENAME="$(basename "${CONFIG_PACKAGE}")"
CONFIG_ID="$(
  printf '%s\n' "${CONFIG_BASENAME}" \
    | sed -nE 's/.*-([A-Za-z0-9_]+)\.(tar\.gz|tgz|tar)$/\1/p'
)"

[[ -n "${CONFIG_ID}" ]] || {
  echo "Failed to extract config id from package name: ${CONFIG_BASENAME}" >&2
  echo "Expected something like: ...-n70201.tgz" >&2
  exit 1
}

IMAGE_COPY="${TMPDIR_WORK}/${CONFIG_ID}-$(basename "${BASE_IMAGE}")"
ATTACHED_DISK=""
ATTACHED_PART=""
MOUNTED=0
KEEP_TMPDIR=0
OS_NAME="$(uname -s)"

platform_attach_image() {
  local image_path="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "${image_path}"
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    losetup --find --show --partscan "${image_path}"
  else
    echo "Unsupported OS: ${OS_NAME}" >&2
    return 1
  fi
}

platform_parse_attached_disk() {
  local attach_output="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    printf '%s\n' "${attach_output}" | awk '$1 ~ /^\/dev\/disk[0-9]+$/ { print $1; exit }'
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    printf '%s\n' "${attach_output}" | awk 'NR==1 {print $1; exit}'
  else
    return 1
  fi
}

platform_first_partition_path() {
  local disk_path="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    printf '%ss1\n' "${disk_path}"
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    if [[ -b "${disk_path}p1" ]]; then
      printf '%sp1\n' "${disk_path}"
    elif [[ -b "${disk_path}1" ]]; then
      printf '%s1\n' "${disk_path}"
    else
      return 1
    fi
  else
    return 1
  fi
}

platform_partition_exists() {
  local part_path="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    diskutil info "${part_path}" >/dev/null 2>&1
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    [[ -b "${part_path}" ]]
  else
    return 1
  fi
}

platform_get_fs_type() {
  local part_path="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    diskutil info "${part_path}" | awk -F': *' '/Type \(Bundle\)/ { print $2; exit }'
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    lsblk -dn -o FSTYPE "${part_path}" 2>/dev/null | head -n1
  else
    return 1
  fi
}

platform_mount_partition() {
  local part_path="$1"
  local mount_dir="$2"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    diskutil mount -mountPoint "${mount_dir}" "${part_path}" >/dev/null
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    mount "${part_path}" "${mount_dir}"
  else
    return 1
  fi
}

platform_unmount_mountpoint() {
  local mount_dir="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    diskutil unmount "${mount_dir}" >/dev/null 2>&1 || true
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    umount "${mount_dir}" >/dev/null 2>&1 || true
  fi
}

platform_detach_image() {
  local disk_path="$1"
  if [[ "${OS_NAME}" == "Darwin" ]]; then
    hdiutil detach "${disk_path}" >/dev/null 2>&1 || true
  elif [[ "${OS_NAME}" == "Linux" ]]; then
    losetup -d "${disk_path}" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  set +e
  if [[ "${MOUNTED}" -eq 1 ]]; then
    platform_unmount_mountpoint "${MOUNT_DIR}"
  fi
  if [[ -n "${ATTACHED_DISK}" ]]; then
    platform_detach_image "${ATTACHED_DISK}"
  fi
  if [[ "${KEEP_TMPDIR}" -ne 1 ]]; then
    rm -rf "${TMPDIR_WORK}"
  fi
}
trap cleanup EXIT

mkdir -p "${MOUNT_DIR}"
cp "${BASE_IMAGE}" "${IMAGE_COPY}"

ATTACH_OUTPUT="$(platform_attach_image "${IMAGE_COPY}")"

ATTACHED_DISK="$(
  platform_parse_attached_disk "${ATTACH_OUTPUT}"
)"

[[ -n "${ATTACHED_DISK}" ]] || {
  echo "Failed to determine attached disk from hdiutil output:" >&2
  printf '%s\n' "${ATTACH_OUTPUT}" >&2
  exit 1
}

if [[ "${OS_NAME}" == "Linux" ]]; then
  for _ in 1 2 3 4 5; do
    ATTACHED_PART="$(platform_first_partition_path "${ATTACHED_DISK}" || true)"
    [[ -n "${ATTACHED_PART}" ]] && platform_partition_exists "${ATTACHED_PART}" && break
    sleep 1
  done
else
  ATTACHED_PART="$(platform_first_partition_path "${ATTACHED_DISK}")"
fi

platform_partition_exists "${ATTACHED_PART}" || {
  echo "First partition not found: ${ATTACHED_PART}" >&2
  echo "attach output:" >&2
  printf '%s\n' "${ATTACH_OUTPUT}" >&2
  exit 1
}

FS_TYPE="$(platform_get_fs_type "${ATTACHED_PART}" | tr '[:upper:]' '[:lower:]')"
if [[ "${FS_TYPE}" != "msdos" && "${FS_TYPE}" != "vfat" && "${FS_TYPE}" != "fat" && "${FS_TYPE}" != "fat32" ]]; then
  echo "Warning: ${ATTACHED_PART} is not reported as FAT/MS-DOS (Type Bundle=${FS_TYPE:-unknown})" >&2
fi

platform_mount_partition "${ATTACHED_PART}" "${MOUNT_DIR}"
MOUNTED=1

cp "${CONFIG_PACKAGE}" "${MOUNT_DIR}/"

sync
KEEP_TMPDIR=1
printf '%s\n' "${IMAGE_COPY}"
