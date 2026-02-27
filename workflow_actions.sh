#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_SCRIPT="${SCRIPT_DIR}/run_host_config_package.sh"
INJECT_SCRIPT="${SCRIPT_DIR}/inject_config_package_to_image.sh"
FLASH_SCRIPT="${SCRIPT_DIR}/flash_image_with_verify.sh"
IMAGES_DIR="${SCRIPT_DIR}/images"
REPO="UforceDev/infra-wl-firmware-builder"
WORKFLOW_ID="host-config-package.yaml"
REF="${GITHUB_REF:-main}"

usage() {
  cat >&2 <<'EOF'
Usage:
  workflow_actions.sh --show-options
  workflow_actions.sh --run-custom --device-path /dev/diskN [--input key=value ...]
EOF
  exit 1
}

show_options() {
  local default_wl_domain="factory.uforce"
  local -a wl_domain_options=()
  local -a base_image_options=()
  local fetched_option

  while IFS= read -r fetched_option; do
    [[ -n "${fetched_option}" ]] || continue
    wl_domain_options+=("${fetched_option}")
  done < <(fetch_wl_domain_options || true)

  if [[ ${#wl_domain_options[@]} -gt 0 ]]; then
    default_wl_domain="${wl_domain_options[0]}"
  fi
  while IFS= read -r fetched_option; do
    [[ -n "${fetched_option}" ]] || continue
    base_image_options+=("${fetched_option}")
  done < <(list_base_images || true)

  cat <<EOF
inputs:
  host_name:
    description: "Имя хоста для сборки config package"
    required: true
    type: string
  wl_domain:
    description: "Домен WL (список из workflow через gh cli)"
    required: false
    default: "${default_wl_domain}"
    type: string
  base_image:
    description: "Базовый .img из папки images"
    required: true
    type: string
EOF

  if [[ ${#wl_domain_options[@]} -gt 0 ]]; then
    echo "    options:"
    local option
    for option in "${wl_domain_options[@]}"; do
      printf '      - "%s"\n' "${option//\"/\\\"}"
    done
  fi

  if [[ ${#base_image_options[@]} -gt 0 ]]; then
    echo "    options:"
    local image_name
    for image_name in "${base_image_options[@]}"; do
      printf '      - "%s"\n' "${image_name//\"/\\\"}"
    done
    printf '    default: "%s"\n' "${base_image_options[0]//\"/\\\"}"
  fi

}

fetch_wl_domain_options() {
  command -v gh >/dev/null 2>&1 || return 1

  if [[ -x "${SCRIPT_DIR}/gh_auth.sh" ]]; then
    "${SCRIPT_DIR}/gh_auth.sh" >/dev/null 2>&1 || return 1
  fi

  local workflow_path content_b64
  workflow_path="$(
    gh api "repos/${REPO}/actions/workflows/${WORKFLOW_ID}" --jq '.path' 2>/dev/null || true
  )"
  [[ -n "${workflow_path}" ]] || return 1

  content_b64="$(
    gh api "repos/${REPO}/contents/${workflow_path}?ref=${REF}" --jq '.content' 2>/dev/null || true
  )"
  [[ -n "${content_b64}" ]] || return 1

  CONTENT_B64="${content_b64}" python3 - <<'PY'
import base64
import os
import sys

raw = os.environ.get("CONTENT_B64", "").replace("\n", "")
if not raw:
    raise SystemExit(1)

try:
    text = base64.b64decode(raw).decode("utf-8", errors="replace")
except Exception:
    raise SystemExit(1)

lines = text.splitlines()

def indent_count(s: str) -> int:
    return len(s) - len(s.lstrip(" "))

def find_child_block(parent_idx: int, key: str):
    parent_indent = indent_count(lines[parent_idx])
    i = parent_idx + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        ind = indent_count(line)
        if ind <= parent_indent:
            return None
        if ind == parent_indent + 2 and stripped == f"{key}:":
            return i
        i += 1
    return None

def find_key_anywhere(key: str):
    for i, line in enumerate(lines):
        if line.strip() == f"{key}:":
            return i
    return None

on_idx = find_key_anywhere("on")
if on_idx is None:
    on_idx = find_key_anywhere('"on"')
if on_idx is None:
    raise SystemExit(1)

workflow_dispatch_idx = find_child_block(on_idx, "workflow_dispatch")
if workflow_dispatch_idx is None:
    raise SystemExit(1)

inputs_idx = find_child_block(workflow_dispatch_idx, "inputs")
if inputs_idx is None:
    raise SystemExit(1)

wl_idx = find_child_block(inputs_idx, "wl_domain")
if wl_idx is None:
    raise SystemExit(1)

options_idx = find_child_block(wl_idx, "options")
if options_idx is None:
    raise SystemExit(1)

base_indent = indent_count(lines[options_idx])
i = options_idx + 1
while i < len(lines):
    line = lines[i]
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        i += 1
        continue
    ind = indent_count(line)
    if ind <= base_indent:
        break
    if stripped.startswith("- "):
        value = stripped[2:].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if value:
            print(value)
    i += 1
PY
}

run_custom() {
  [[ -f "${RUNNER_SCRIPT}" ]] || {
    echo "Script not found: ${RUNNER_SCRIPT}" >&2
    exit 1
  }

  local host_name=""
  local wl_domain=""
  local device_path=""
  local base_image_input=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --device-path)
        [[ $# -ge 2 ]] || usage
        device_path="$2"
        shift 2
        ;;
      --input)
        [[ $# -ge 2 ]] || usage
        case "$2" in
          host_name=*) host_name="${2#host_name=}" ;;
          wl_domain=*) wl_domain="${2#wl_domain=}" ;;
          base_image=*) base_image_input="${2#base_image=}" ;;
        esac
        shift 2
        ;;
      *)
        usage
        ;;
    esac
  done

  if [[ -z "${host_name}" ]]; then
    echo "Missing required input: host_name" >&2
    exit 1
  fi
  if [[ -z "${device_path}" ]]; then
    echo "Missing required argument: --device-path" >&2
    exit 1
  fi
  if [[ -z "${base_image_input}" ]]; then
    echo "Missing required input: base_image" >&2
    exit 1
  fi

  if [[ -n "${wl_domain}" ]]; then
    export WL_DOMAIN="${wl_domain}"
  fi

  local base_image=""
  if [[ -f "${base_image_input}" ]]; then
    base_image="${base_image_input}"
  elif [[ -f "${IMAGES_DIR}/${base_image_input}" ]]; then
    base_image="${IMAGES_DIR}/${base_image_input}"
  else
    echo "Selected base image not found: ${base_image_input}" >&2
    exit 1
  fi

  echo "=== Step 1/3: Generate and download config package ==="
  /bin/bash "${RUNNER_SCRIPT}" "${host_name}"

  local config_package generated_image

  config_package="$(find_latest_config_package)"
  [[ -n "${config_package}" ]] || {
    echo "No config package found in ${IMAGES_DIR}" >&2
    exit 1
  }
  echo "Selected config package: ${config_package}"

  echo "Selected base image: ${base_image}"

  [[ -f "${INJECT_SCRIPT}" ]] || {
    echo "Script not found: ${INJECT_SCRIPT}" >&2
    exit 1
  }
  [[ -f "${FLASH_SCRIPT}" ]] || {
    echo "Script not found: ${FLASH_SCRIPT}" >&2
    exit 1
  }

  echo "=== Step 2/3: Generate image with config package ==="
  generated_image="$(/bin/bash "${INJECT_SCRIPT}" "${base_image}" "${config_package}")"
  generated_image="$(printf '%s' "${generated_image}" | tail -n1)"
  [[ -n "${generated_image}" ]] || {
    echo "inject_config_package_to_image.sh did not return output image path" >&2
    exit 1
  }
  echo "Generated image: ${generated_image}"

  echo "=== Step 3/3: Flash image to ${device_path} ==="
  /bin/bash "${FLASH_SCRIPT}" "${device_path}" "${generated_image}"
  echo "Flashing completed successfully."
}

find_latest_config_package() {
  [[ -d "${IMAGES_DIR}" ]] || return 1
  local newest=""
  local candidate
  for candidate in \
    "${IMAGES_DIR}"/ufg_config_package-*.tgz \
    "${IMAGES_DIR}"/ufg_config_package-*.tar.gz \
    "${IMAGES_DIR}"/ufg_config_package-*.tar
  do
    [[ -f "${candidate}" ]] || continue
    if [[ -z "${newest}" || "${candidate}" -nt "${newest}" ]]; then
      newest="${candidate}"
    fi
  done
  [[ -n "${newest}" ]] || return 1
  printf '%s\n' "${newest}"
}

list_base_images() {
  [[ -d "${IMAGES_DIR}" ]] || return 1
  local candidate
  for candidate in "${IMAGES_DIR}"/*.img; do
    [[ -f "${candidate}" ]] || continue
    basename "${candidate}"
  done
}

main() {
  [[ $# -ge 1 ]] || usage

  case "$1" in
    --show-options)
      [[ $# -eq 1 ]] || usage
      show_options
      ;;
    --run-custom)
      shift
      run_custom "$@"
      ;;
    *)
      usage
      ;;
  esac
}

main "$@"
