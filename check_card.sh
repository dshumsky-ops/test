#!/usr/bin/env bash
set -euo pipefail

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

platform_name() {
  uname -s
}

list_external_disks() {
  case "$(platform_name)" in
    Darwin)
      diskutil list external physical 2>/dev/null | awk '/^\/dev\/disk[0-9]+/ {print $1}'
      ;;
    Linux)
      lsblk -dn -o PATH,TYPE,TRAN,RM 2>/dev/null \
        | awk '$2=="disk" && ($3=="usb" || $4=="1") {print $1}'
      ;;
    *)
      return 1
      ;;
  esac
}

get_disk_info_fields() {
  local dev="$1"
  case "$(platform_name)" in
    Darwin)
      local info protocol size manufacturer
      info="$(diskutil info "$dev" 2>/dev/null || true)"
      [[ -n "$info" ]] || return 1
      protocol="$(printf '%s\n' "$info" | awk -F: '/^[[:space:]]*Protocol:/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')"
      size="$(printf '%s\n' "$info" | awk -F: '/^[[:space:]]*Disk Size:/ {sub(/^[[:space:]]+/, "", $2); sub(/[[:space:]]*\(.*/, "", $2); print $2; exit}')"
      manufacturer="$(printf '%s\n' "$info" | awk -F: '
        /^[[:space:]]*Device Manufacturer:/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}
        /^[[:space:]]*Device \/ Media Name:/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}
        /^[[:space:]]*Media Name:/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}
      ')"
      protocol="$(trim "$protocol")"
      size="$(trim "$size")"
      manufacturer="$(trim "$manufacturer")"
      printf '%s\t%s\t%s\n' "$protocol" "$size" "$manufacturer"
      ;;
    Linux)
      local protocol size vendor model manufacturer
      protocol="$(lsblk -dn -o TRAN "$dev" 2>/dev/null | head -n1)"
      size="$(lsblk -dn -o SIZE "$dev" 2>/dev/null | head -n1)"
      vendor="$(lsblk -dn -o VENDOR "$dev" 2>/dev/null | head -n1)"
      model="$(lsblk -dn -o MODEL "$dev" 2>/dev/null | head -n1)"
      protocol="$(trim "$protocol")"
      size="$(trim "$size")"
      vendor="$(trim "$vendor")"
      model="$(trim "$model")"
      manufacturer="$(trim "${vendor} ${model}")"
      printf '%s\t%s\t%s\n' "$protocol" "$size" "$manufacturer"
      ;;
    *)
      return 1
      ;;
  esac
}

main() {
  case "$(platform_name)" in
    Darwin|Linux) ;;
    *)
      echo "Скрипт поддерживается только на macOS (Darwin) и Linux." >&2
      exit 1
      ;;
  esac

  local dev fields protocol size manufacturer
  while IFS= read -r dev; do
    [[ -n "$dev" ]] || continue
    fields="$(get_disk_info_fields "$dev" || true)"
    [[ -n "$fields" ]] || continue
    IFS=$'\t' read -r protocol size manufacturer <<<"$fields"
    protocol="$(trim "$protocol")"
    size="$(trim "$size")"
    manufacturer="$(trim "$manufacturer")"

    case "$(platform_name)" in
      Darwin)
        [[ "$protocol" == "USB" ]] || continue
        ;;
      Linux)
        [[ "$protocol" == "usb" || -z "$protocol" ]] || continue
        ;;
    esac

    printf '%s\t%s\t%s\n' "$dev" "$size" "$manufacturer"
  done < <(list_external_disks)

  return 0
}

main "$@"
