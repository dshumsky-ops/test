#!/usr/bin/env bash
set -euo pipefail

TOKEN_FILE="${HOME}/.config/ufg-baker-gh-token"
mkdir -p "$(dirname "$TOKEN_FILE")"

validate_token() {
  local token="$1"
  local code
  code="$(
    curl -sS -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer ${token}" \
      -H "Accept: application/vnd.github+json" \
      https://api.github.com/user || true
  )"
  [[ "$code" == "200" ]]
}

normalize_token() {
  local t="$1"
  t="${t//$'\r'/}"
  t="${t#"${t%%[![:space:]]*}"}"
  t="${t%"${t##*[![:space:]]}"}"
  printf '%s' "$t"
}

save_token() {
  local token
  token="$(normalize_token "${1:-}")"
  [[ -n "$token" ]] || return 1
  printf '%s\n' "$token" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "Token сохранен в $TOKEN_FILE" >&2
}

read_saved_token() {
  [[ -r "$TOKEN_FILE" ]] || return 1
  local t
  t="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
  t="$(normalize_token "$t")"
  [[ -n "$t" ]] || return 1
  printf '%s' "$t"
}

read_gh_cli_token() {
  command -v gh >/dev/null 2>&1 || return 1
  local t
  t="$(gh auth token --hostname github.com 2>/dev/null || true)"
  t="$(normalize_token "$t")"
  [[ -n "$t" ]] || return 1
  printf '%s' "$t"
}

login_via_github_browser() {
  echo "Открываю авторизацию GitHub через браузер..." >&2
  gh auth login --hostname github.com --web --git-protocol https >&2

  local token
  token="$(read_gh_cli_token || true)"
  [[ -n "$token" ]] || {
    echo "Не удалось получить token из gh." >&2
    exit 1
  }

  validate_token "$token" || {
    echo "Полученный token невалидный." >&2
    exit 1
  }

  save_token "$token"
}

main() {
  local token=""

  if token="$(read_saved_token 2>/dev/null)"; then
    if validate_token "$token"; then
      echo "GitHub token валидный." >&2
      exit 0
    fi
    echo "Сохраненный token невалидный/expired." >&2
  else
    echo "Token не найден." >&2
  fi

  if token="$(read_gh_cli_token 2>/dev/null)"; then
    if validate_token "$token"; then
      echo "Найден активный token в gh cli. Сохраняю в файл..." >&2
      save_token "$token"
      exit 0
    fi
  fi

  login_via_github_browser
}

main "$@"
