#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${DOCUPARSE_REPO:-/root/docuparse2.0}"
BRANCH="${DOCUPARSE_BRANCH:-main}"
LOCK_FILE="${DOCUPARSE_DEPLOY_LOCK_FILE:-/tmp/docuparse-digitalocean-deploy.lock}"
HEALTH_URL="${DOCUPARSE_HEALTH_URL:-http://127.0.0.1:8001/health}"
REQUIRE_VL_PRIMARY="${REQUIRE_VL_PRIMARY:-1}"
FORCE_REBUILD="${FORCE_REBUILD:-0}"

SERVICES=(backend backend-worker ocr-worker frontend)
if [[ -n "${DOCUPARSE_COMPOSE_SERVICES:-}" ]]; then
  # shellcheck disable=SC2206
  SERVICES=(${DOCUPARSE_COMPOSE_SERVICES})
fi

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

echo "[$(timestamp)] DigitalOcean deploy starting"
cd "$REPO_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(timestamp)] another deploy is already running; exiting"
  exit 0
fi

if [[ -n "$(git status --porcelain=v1 -uno)" && "${ALLOW_DIRTY_DEPLOY:-0}" != "1" ]]; then
  echo "Tracked working tree is dirty. Refusing automated deploy." >&2
  git status --short -uno >&2
  exit 1
fi

old_head="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
new_head="$(git rev-parse HEAD)"

echo "old_head=$old_head"
echo "new_head=$new_head"

docker compose config >/dev/null

if [[ -x scripts/start-do-runpod-forward.sh ]]; then
  scripts/start-do-runpod-forward.sh
fi

if [[ "$old_head" != "$new_head" || "$FORCE_REBUILD" == "1" ]]; then
  echo "[$(timestamp)] rebuilding services: ${SERVICES[*]}"
  docker compose --profile backend up -d --build "${SERVICES[@]}"
else
  echo "[$(timestamp)] no Git change; leaving running containers in place"
  docker compose ps
fi

echo "[$(timestamp)] backend health"
HEALTH_JSON="$(curl -fsS --max-time 20 "$HEALTH_URL")"
HEALTH_JSON="$HEALTH_JSON" python3 - "$REQUIRE_VL_PRIMARY" <<'PY'
import json
import os
import sys

require_vl_primary = sys.argv[1] == "1"
payload = json.loads(os.environ["HEALTH_JSON"])
providers = payload.get("providers", {})
gguf = providers.get("paddleocr_vl_gguf") or {}
summary = {
    "status": payload.get("status"),
    "ocr_engine": providers.get("ocr_engine"),
    "primary_provider_status": providers.get("primary_provider_status"),
    "primary_reader_available": providers.get("primary_reader_available"),
    "fallback_reason": providers.get("fallback_reason"),
    "worker_location": gguf.get("worker_location"),
    "worker_transport": gguf.get("worker_transport"),
    "worker_health": (gguf.get("worker_health") or {}).get("status"),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
if payload.get("status") != "ok":
    raise SystemExit("backend health is not ok")
if require_vl_primary:
    if providers.get("primary_reader_available") is not True:
        raise SystemExit("VL primary reader is not available")
    if providers.get("fallback_reason"):
        raise SystemExit(f"backend reports fallback_reason={providers.get('fallback_reason')}")
PY

if [[ -x scripts/check-vl-worker.sh ]]; then
  BACKEND_HEALTH_URL="$HEALTH_URL" scripts/check-vl-worker.sh || true
fi

echo "[$(timestamp)] DigitalOcean deploy finished"
