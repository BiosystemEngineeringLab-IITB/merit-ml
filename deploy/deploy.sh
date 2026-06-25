#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONFIG_FILE="${SCRIPT_DIR}/config.env"
CRED_FILE="${SCRIPT_DIR}/credentials.env"

OVERRIDE_CACHE_ROOT=""
OVERRIDE_R2_PREFIX=""
SKIP_SYNC=0
SKIP_VERCEL_ENV=0
SKIP_DEPLOY=0

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [options]

Options:
  --cache-root PATH      Override CACHE_ROOT from config.env
  --r2-prefix PREFIX     Override R2_PREFIX from config.env
  --skip-sync            Skip Cloudflare R2 sync
  --skip-vercel-env      Skip Vercel env update
  --skip-deploy          Skip Vercel production deploy
  -h, --help             Show help

Examples:
  ./deploy/deploy.sh --r2-prefix "merit-cache/v6-2026-04-10"
  ./deploy/deploy.sh --skip-sync --skip-vercel-env
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cache-root)
      OVERRIDE_CACHE_ROOT="${2:-}"
      shift 2
      ;;
    --r2-prefix)
      OVERRIDE_R2_PREFIX="${2:-}"
      shift 2
      ;;
    --skip-sync)
      SKIP_SYNC=1
      shift
      ;;
    --skip-vercel-env)
      SKIP_VERCEL_ENV=1
      shift
      ;;
    --skip-deploy)
      SKIP_DEPLOY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Missing ${CONFIG_FILE}. Copy deploy/config.env.template to deploy/config.env first."
  exit 1
fi

if [[ ! -f "${CRED_FILE}" ]]; then
  echo "Missing ${CRED_FILE}. Copy deploy/credentials.env.template to deploy/credentials.env first."
  exit 1
fi

# shellcheck source=/dev/null
source "${CONFIG_FILE}"
# shellcheck source=/dev/null
source "${CRED_FILE}"

PROJECT_ROOT="${PROJECT_ROOT:-${ROOT_DIR}}"
CACHE_ROOT="${OVERRIDE_CACHE_ROOT:-${CACHE_ROOT:-}}"
R2_PREFIX="${OVERRIDE_R2_PREFIX:-${R2_PREFIX:-}}"

if [[ -z "${CACHE_ROOT}" || -z "${R2_PREFIX}" ]]; then
  echo "CACHE_ROOT and R2_PREFIX must be set (config or CLI override)."
  exit 1
fi

if [[ ! -d "${CACHE_ROOT}" ]]; then
  echo "CACHE_ROOT not found: ${CACHE_ROOT}"
  exit 1
fi

if [[ ! -f "${CACHE_ROOT}/index.json" ]]; then
  echo "Missing ${CACHE_ROOT}/index.json"
  exit 1
fi

if [[ ! -d "${CACHE_ROOT}/json" ]]; then
  echo "Missing ${CACHE_ROOT}/json directory"
  exit 1
fi

command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v vercel >/dev/null || { echo "vercel CLI not found"; exit 1; }

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${PROJECT_ROOT}/.cache}"

mkdir -p "${XDG_CACHE_HOME}"

R2_PREFIX_CLEAN="${R2_PREFIX#/}"
R2_PREFIX_CLEAN="${R2_PREFIX_CLEAN%/}"
PUBLIC_ROOT_CLEAN="${R2_PUBLIC_ROOT%/}"
TARGET_PUBLIC_ROOT="${PUBLIC_ROOT_CLEAN}/${R2_PREFIX_CLEAN}/"

echo "== MERIT Deploy Config =="
echo "PROJECT_ROOT      : ${PROJECT_ROOT}"
echo "CACHE_ROOT        : ${CACHE_ROOT}"
echo "R2_ENDPOINT       : ${R2_ENDPOINT}"
echo "R2_BUCKET         : ${R2_BUCKET}"
echo "R2_PREFIX         : ${R2_PREFIX_CLEAN}"
echo "PUBLIC_CACHE_ROOT : ${TARGET_PUBLIC_ROOT}"
echo "VERCEL_ENV        : ${VERCEL_ENV}"
echo "MERIT_CACHE_ONLY  : ${MERIT_CACHE_ONLY}"
echo

if [[ "${SKIP_SYNC}" -eq 0 ]]; then
  echo "[1/3] Sync cache to Cloudflare R2..."
  aws s3 sync "${CACHE_ROOT}" "s3://${R2_BUCKET}/${R2_PREFIX_CLEAN}/" \
    --endpoint-url "${R2_ENDPOINT}" \
    --only-show-errors

  echo "Verifying index on R2..."
  aws s3 ls "s3://${R2_BUCKET}/${R2_PREFIX_CLEAN}/index.json" \
    --endpoint-url "${R2_ENDPOINT}" >/dev/null
else
  echo "[1/3] Skipped R2 sync."
fi

if [[ "${SKIP_VERCEL_ENV}" -eq 0 ]]; then
  echo "[2/3] Update Vercel environment variables..."
  pushd "${PROJECT_ROOT}" >/dev/null

  vercel env rm MERIT_PRECOMPUTED_ROOT "${VERCEL_ENV}" --yes >/dev/null 2>&1 || true
  printf "%s\n" "${TARGET_PUBLIC_ROOT}" | vercel env add MERIT_PRECOMPUTED_ROOT "${VERCEL_ENV}" >/dev/null

  vercel env rm MERIT_CACHE_ONLY "${VERCEL_ENV}" --yes >/dev/null 2>&1 || true
  printf "%s\n" "${MERIT_CACHE_ONLY}" | vercel env add MERIT_CACHE_ONLY "${VERCEL_ENV}" >/dev/null

  popd >/dev/null
else
  echo "[2/3] Skipped Vercel env update."
fi

DEPLOY_URL=""
if [[ "${SKIP_DEPLOY}" -eq 0 ]]; then
  echo "[3/3] Deploy to Vercel production..."
  pushd "${PROJECT_ROOT}" >/dev/null

  DEPLOY_OUT="$(vercel --prod --yes)"
  echo "${DEPLOY_OUT}"
  DEPLOY_URL="$(printf "%s\n" "${DEPLOY_OUT}" | rg -o 'https://[a-zA-Z0-9.-]+\\.vercel\\.app' | tail -n 1 || true)"

  popd >/dev/null
else
  echo "[3/3] Skipped Vercel deploy."
fi

echo
echo "== Done =="
echo "Cache root (Vercel env): ${TARGET_PUBLIC_ROOT}"
if [[ -n "${DEPLOY_URL}" ]]; then
  echo "Deploy URL: ${DEPLOY_URL}"
  echo "Health check:"
  set +e
  curl -fsSL "${DEPLOY_URL}/healthz" && echo
  set -e
fi

