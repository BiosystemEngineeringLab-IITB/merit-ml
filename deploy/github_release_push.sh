#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

RELEASE_DIR="${MERIT_RELEASE_REPO_DIR:-/tmp/merit-ml-release-sync}"
REMOTE="${MERIT_RELEASE_REMOTE:-git@github.com-merit-ml:BiosystemEngineeringLab-IITB/merit-ml.git}"
HTTPS_REMOTE="https://github.com/BiosystemEngineeringLab-IITB/merit-ml.git"
BRANCH="${MERIT_RELEASE_BRANCH:-main}"
COMMIT_MESSAGE="Sync MERIT-ML release files"
DRY_RUN=0
NO_PUSH=0

usage() {
  cat <<USAGE
Usage:
  ./deploy/github_release_push.sh [options]

Options:
  --message TEXT     Commit message. Required for real releases.
  --dry-run          Copy/check/diff only; do not commit or push.
  --no-push          Commit locally but do not push.
  -h, --help         Show help.

Environment overrides:
  MERIT_RELEASE_REPO_DIR   Release checkout path. Default: /tmp/merit-ml-release-sync
  MERIT_RELEASE_REMOTE     Git remote. Default: git@github.com-merit-ml:BiosystemEngineeringLab-IITB/merit-ml.git
  MERIT_RELEASE_BRANCH     Branch. Default: main
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message)
      COMMIT_MESSAGE="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-push)
      NO_PUSH=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

command -v git >/dev/null || { echo "git not found" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync not found" >&2; exit 1; }

if [[ ! -d "${ROOT_DIR}/merit-ui-v2" ]]; then
  echo "Could not find merit-ui-v2 under ${ROOT_DIR}" >&2
  exit 1
fi

case "${COMMIT_MESSAGE}" in
  ""|"Sync MERIT-ML release files")
    if [[ "${DRY_RUN}" -eq 0 ]]; then
      echo "Use --message with a descriptive release commit message." >&2
      exit 1
    fi
    ;;
esac

clone_or_update_release_repo() {
  if [[ ! -d "${RELEASE_DIR}/.git" ]]; then
    mkdir -p "$(dirname "${RELEASE_DIR}")"
    echo "Cloning public release repository into ${RELEASE_DIR}"
    if ! git clone --branch "${BRANCH}" "${REMOTE}" "${RELEASE_DIR}"; then
      echo "SSH clone failed; trying HTTPS remote for read-only clone. Push may still require auth." >&2
      git clone --branch "${BRANCH}" "${HTTPS_REMOTE}" "${RELEASE_DIR}"
      git -C "${RELEASE_DIR}" remote set-url origin "${REMOTE}" || true
    fi
  else
    echo "Updating existing release checkout: ${RELEASE_DIR}"
    git -C "${RELEASE_DIR}" fetch origin "${BRANCH}"
    git -C "${RELEASE_DIR}" checkout "${BRANCH}"
    git -C "${RELEASE_DIR}" pull --rebase origin "${BRANCH}"
    git -C "${RELEASE_DIR}" remote set-url origin "${REMOTE}" || true
  fi
}

copy_file() {
  local rel="$1"
  if [[ -f "${ROOT_DIR}/${rel}" ]]; then
    mkdir -p "${RELEASE_DIR}/$(dirname "${rel}")"
    rsync -a "${ROOT_DIR}/${rel}" "${RELEASE_DIR}/${rel}"
  fi
}

copy_dir() {
  local rel="$1"
  if [[ -d "${ROOT_DIR}/${rel}" ]]; then
    mkdir -p "${RELEASE_DIR}/${rel}"
    rsync -a --delete \
      --exclude '.git/' \
      --exclude '.vercel/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '.pytest_cache/' \
      --exclude '.mypy_cache/' \
      --exclude '.ruff_cache/' \
      --exclude 'credentials.env' \
      --exclude 'config.env' \
      --exclude 'cloudfare' \
      --exclude 'cloudflare' \
      --exclude '*.token' \
      --exclude '*secret*' \
      --exclude '*credential*' \
      "${ROOT_DIR}/${rel}/" "${RELEASE_DIR}/${rel}/"
  fi
}

copy_deploy_whitelist() {
  mkdir -p "${RELEASE_DIR}/deploy"
  local files=(
    ".gitignore"
    "README.md"
    "MERIT_DEPLOYMENT_RUNBOOK.md"
    "AGENT_RELEASE_AUTOMATION.md"
    "GITHUB_PUSH_AUTOMATION.md"
    "VERCEL_CICD_MERIT.md"
    "config.env.template"
    "credentials.env.template"
    "deploy.sh"
    "github_release_push.sh"
  )
  local rel
  for rel in "${files[@]}"; do
    if [[ -f "${ROOT_DIR}/deploy/${rel}" ]]; then
      rsync -a "${ROOT_DIR}/deploy/${rel}" "${RELEASE_DIR}/deploy/${rel}"
    fi
  done
}

assert_no_forbidden_release_paths() {
  local forbidden
  forbidden="$({
    find "${RELEASE_DIR}" -maxdepth 4 \
      \( -name 'merit-cache-workbench-full-v*' \
      -o -name 'mw-dump-latest-confirmation*' \
      -o -name 'merit-cache-metabolights-fetch-v1' \
      -o -name 'credentials.env' \
      -o -name 'config.env' \
      -o -name '*.token' \
      -o -name '*secret*' \
      -o -path '*/.vercel/project.json' \) -print
  } || true)"

  if [[ -n "${forbidden}" ]]; then
    echo "Forbidden release paths detected; refusing to continue:" >&2
    printf '%s\n' "${forbidden}" >&2
    exit 1
  fi
}

clone_or_update_release_repo

copy_file "README.md"
copy_file ".dockerignore"
copy_file "docker-compose.merit.yml"
copy_dir "docker"
copy_dir "merit-ui-v2"
copy_deploy_whitelist

assert_no_forbidden_release_paths

echo
printf 'Release checkout: %s\n' "${RELEASE_DIR}"
printf 'Remote: %s\n' "$(git -C "${RELEASE_DIR}" remote get-url origin)"
echo

git -C "${RELEASE_DIR}" status --short

echo
if git -C "${RELEASE_DIR}" diff --quiet --exit-code && git -C "${RELEASE_DIR}" diff --cached --quiet --exit-code; then
  echo "No release-safe changes detected. Nothing to commit."
  exit 0
fi

echo "Diff summary:"
git -C "${RELEASE_DIR}" diff --stat

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo
  echo "Dry run requested; not committing or pushing."
  exit 0
fi

# Keep author identity local to the release clone. Override with env vars if desired.
git -C "${RELEASE_DIR}" config user.name "${GIT_AUTHOR_NAME:-Codex}"
git -C "${RELEASE_DIR}" config user.email "${GIT_AUTHOR_EMAIL:-codex@openai.com}"

git -C "${RELEASE_DIR}" add README.md .dockerignore docker-compose.merit.yml docker merit-ui-v2 deploy
git -C "${RELEASE_DIR}" commit -m "${COMMIT_MESSAGE}"

if [[ "${NO_PUSH}" -eq 1 ]]; then
  echo "Committed locally only (--no-push)."
  git -C "${RELEASE_DIR}" log --oneline -1
  exit 0
fi

git -C "${RELEASE_DIR}" push origin "${BRANCH}"
echo
printf 'Pushed commit: '
git -C "${RELEASE_DIR}" rev-parse --short HEAD
