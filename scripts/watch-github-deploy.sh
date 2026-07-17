#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-${DOCUPARSE_DEPLOY_ROLE:-}}"
BRANCH="${DOCUPARSE_BRANCH:-main}"
INTERVAL_SECONDS="${DOCUPARSE_AUTO_SYNC_INTERVAL_SECONDS:-60}"

case "$ROLE" in
  digitalocean)
    REPO_DIR="${DOCUPARSE_REPO:-/root/docuparse2.0}"
    DEPLOY_SCRIPT="${DOCUPARSE_DEPLOY_SCRIPT:-$REPO_DIR/scripts/deploy-digitalocean.sh}"
    ;;
  runpod)
    REPO_DIR="${DOCUPARSE_REPO:-/workspace/DocuParse}"
    DEPLOY_SCRIPT="${DOCUPARSE_DEPLOY_SCRIPT:-$REPO_DIR/scripts/deploy-runpod.sh}"
    ;;
  *)
    echo "Usage: $0 <digitalocean|runpod>" >&2
    exit 2
    ;;
esac

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

echo "[$(timestamp)] watching GitHub branch '$BRANCH' for $ROLE deploys"
echo "repo=$REPO_DIR"
echo "deploy_script=$DEPLOY_SCRIPT"
echo "interval=${INTERVAL_SECONDS}s"

while true; do
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "[$(timestamp)] missing git repo: $REPO_DIR" >&2
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  cd "$REPO_DIR"
  local_head="$(git rev-parse HEAD 2>/dev/null || true)"
  remote_head="$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | awk '{print $1}' || true)"

  if [[ -z "$remote_head" ]]; then
    echo "[$(timestamp)] could not read origin/$BRANCH"
  elif [[ "$remote_head" != "$local_head" ]]; then
    echo "[$(timestamp)] remote changed: local=$local_head remote=$remote_head"
    "$DEPLOY_SCRIPT" || echo "[$(timestamp)] deploy failed with exit code $?" >&2
  else
    echo "[$(timestamp)] up to date: $local_head"
  fi

  sleep "$INTERVAL_SECONDS"
done
