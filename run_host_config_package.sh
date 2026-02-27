#!/usr/bin/env bash
set -euo pipefail

REPO="UforceDev/infra-wl-firmware-builder"
WORKFLOW_ID="host-config-package.yaml"
TOKEN_FILE="${HOME}/.config/ufg-baker-gh-token"
REF="${GITHUB_REF:-main}"
WL_DOMAIN="factory.uforce"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
RUN_DISCOVERY_TIMEOUT_SEC="${RUN_DISCOVERY_TIMEOUT_SEC:-120}"

usage() {
  echo "Usage: $0 <host_name>" >&2
  exit 1
}

[[ $# -eq 1 ]] || usage
HOST_NAME="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="${SCRIPT_DIR}/images"

"${SCRIPT_DIR}/gh_auth.sh" >/dev/null

TOKEN="$(<"${TOKEN_FILE}")"
TOKEN="${TOKEN//$'\r'/}"
TOKEN="${TOKEN#"${TOKEN%%[![:space:]]*}"}"
TOKEN="${TOKEN%"${TOKEN##*[![:space:]]}"}"

gh_api() {
  GH_TOKEN="${TOKEN}" gh api "$@"
}

gh_cmd() {
  GH_TOKEN="${TOKEN}" gh "$@"
}

get_latest_run_id() {
  gh_cmd run list \
    --repo "${REPO}" \
    --workflow "${WORKFLOW_ID}" \
    --branch "${REF}" \
    --event workflow_dispatch \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId // 0'
}

PAYLOAD="$(cat <<EOF
{
  "ref": "${REF}",
  "inputs": {
    "host_name": "${HOST_NAME}",
    "wl_domain": "${WL_DOMAIN}"
  }
}
EOF
)"

PREV_RUN_ID="$(get_latest_run_id 2>/dev/null || echo 0)"

HTTP_CODE="$(
  curl -sS -o /tmp/host-config-package-response.$$ -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_ID}/dispatches" \
    -d "${PAYLOAD}" || true
)"

if [[ "${HTTP_CODE}" != "204" ]]; then
  echo "Failed to trigger workflow (HTTP ${HTTP_CODE})" >&2
  cat /tmp/host-config-package-response.$$ >&2 || true
  rm -f /tmp/host-config-package-response.$$ || true
  exit 1
fi

rm -f /tmp/host-config-package-response.$$ || true

echo "Workflow triggered successfully. Waiting for run..."
echo "Repo: ${REPO}"
echo "Workflow: ${WORKFLOW_ID}"
echo "ref: ${REF}"
echo "host_name: ${HOST_NAME}"
echo "wl_domain: ${WL_DOMAIN}"

RUN_ID=""
deadline=$((SECONDS + RUN_DISCOVERY_TIMEOUT_SEC))
while (( SECONDS < deadline )); do
  RUN_ID="$(
    gh_cmd run list \
      --repo "${REPO}" \
      --workflow "${WORKFLOW_ID}" \
      --branch "${REF}" \
      --event workflow_dispatch \
      --limit 20 \
      --json databaseId \
      --jq "map(select(.databaseId > ${PREV_RUN_ID})) | sort_by(.databaseId) | last | .databaseId // empty" \
      2>/dev/null || true
  )"

  if [[ -n "${RUN_ID}" ]]; then
    break
  fi

  sleep "${POLL_INTERVAL_SEC}"
done

if [[ -z "${RUN_ID}" ]]; then
  echo "Could not find a new workflow run within ${RUN_DISCOVERY_TIMEOUT_SEC}s." >&2
  echo "Open manually: https://github.com/${REPO}/actions/workflows/${WORKFLOW_ID}" >&2
  exit 1
fi

RUN_URL="https://github.com/${REPO}/actions/runs/${RUN_ID}"
echo "Run ID: ${RUN_ID}"
echo "Run URL: ${RUN_URL}"

if ! gh_cmd run watch "${RUN_ID}" --repo "${REPO}" --exit-status; then
  echo "Workflow completed with failure. ${RUN_URL}" >&2
  exit 1
fi

echo "Workflow completed successfully. Reading logs..."

LOG_FILE="/tmp/host-config-package-run-${RUN_ID}.log"
gh_cmd run view "${RUN_ID}" --repo "${REPO}" --log > "${LOG_FILE}"

CONFIG_URL="$(
  grep -Eo 'https://[^[:space:]]+' "${LOG_FILE}" \
    | grep 'config-packages/' \
    | grep -F "/${WL_DOMAIN}/" \
    | grep 'ufg_config_package-.*\.tgz' \
    | tail -n1 || true
)"

if [[ -z "${CONFIG_URL}" ]]; then
  echo "Could not find config package URL in logs." >&2
  echo "Run URL: ${RUN_URL}" >&2
  echo "Log file: ${LOG_FILE}" >&2
  exit 1
fi

OUT_FILE="$(basename "${CONFIG_URL%%\?*}")"
mkdir -p "${IMAGES_DIR}"
OUT_PATH="${IMAGES_DIR}/${OUT_FILE}"
echo "Downloading: ${OUT_PATH}"
curl -fL --retry 3 --retry-delay 2 -o "${OUT_PATH}" "${CONFIG_URL}"
echo "Downloaded: ${OUT_PATH}"
