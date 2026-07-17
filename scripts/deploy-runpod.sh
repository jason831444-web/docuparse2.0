#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${DOCUPARSE_REPO:-/workspace/DocuParse}"
BRANCH="${DOCUPARSE_BRANCH:-main}"
RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"
LOCK_FILE="${DOCUPARSE_DEPLOY_LOCK_FILE:-/tmp/docuparse-runpod-deploy.lock}"
BACKEND_PORT="${RUNPOD_BACKEND_PORT:-8000}"
FRONTEND_PORT="${RUNPOD_FRONTEND_PORT:-8888}"
REQUIRE_VL_PRIMARY="${REQUIRE_VL_PRIMARY:-1}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

stop_port_listener() {
  local label="$1"
  local port="$2"
  local pids
  pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    echo "no $label listener on port $port"
    return 0
  fi
  echo "stopping $label listener(s) on port $port: $pids"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in {1..20}; do
    if [[ -z "$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "warning: $label listener still appears to be bound to $port" >&2
}

start_backend() {
  if [[ ! -x "$REPO_DIR/backend/.venv/bin/uvicorn" ]]; then
    echo "missing backend venv uvicorn: $REPO_DIR/backend/.venv/bin/uvicorn" >&2
    return 1
  fi
  mkdir -p "$LOG_DIR"
  (
    cd "$REPO_DIR/backend"
    DATABASE_URL="${RUNPOD_DATABASE_URL:-postgresql+psycopg://docuparse:docuparse@localhost:5432/docuparse}" \
      PYTHONPATH="$REPO_DIR/backend" \
      nohup .venv/bin/uvicorn app.main:app \
        --host 127.0.0.1 \
        --port "$BACKEND_PORT" \
        > "$LOG_DIR/runpod_backend_stdout.log" \
        2> "$LOG_DIR/runpod_backend_stderr.log" &
    echo "$!" > "$LOG_DIR/runpod_backend.pid"
  )
}

start_frontend() {
  if [[ ! -d "$REPO_DIR/frontend/node_modules" ]]; then
    echo "missing frontend node_modules: $REPO_DIR/frontend/node_modules" >&2
    return 1
  fi
  mkdir -p "$LOG_DIR"
  (
    cd "$REPO_DIR/frontend"
    DOCUPARSE_BACKEND_INTERNAL_URL="${DOCUPARSE_BACKEND_INTERNAL_URL:-http://127.0.0.1:$BACKEND_PORT}" \
      NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-/api}" \
      nohup npm run dev -- --hostname 0.0.0.0 --port "$FRONTEND_PORT" \
        > "$LOG_DIR/runpod_frontend_${FRONTEND_PORT}_stdout.log" \
        2> "$LOG_DIR/runpod_frontend_${FRONTEND_PORT}_stderr.log" &
    echo "$!" > "$LOG_DIR/runpod_frontend_${FRONTEND_PORT}.pid"
  )
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local attempts="${3:-60}"
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      echo "ok: $label"
      return 0
    fi
    sleep 2
  done
  echo "failed: $label did not become reachable at $url" >&2
  return 1
}

echo "[$(timestamp)] RunPod deploy starting"
mkdir -p "$LOG_DIR"
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

changed_files=""
if [[ "$old_head" != "$new_head" ]]; then
  changed_files="$(git diff --name-only "$old_head" "$new_head" || true)"
fi

backend_deps_changed=0
frontend_deps_changed=0
if grep -Eq '^backend/requirements.*\.txt$' <<< "$changed_files"; then
  backend_deps_changed=1
fi
if grep -Eq '^frontend/(package-lock\.json|package\.json)$' <<< "$changed_files"; then
  frontend_deps_changed=1
fi

if [[ "$backend_deps_changed" == "1" && -x "$REPO_DIR/backend/.venv/bin/pip" ]]; then
  echo "[$(timestamp)] updating backend venv dependencies"
  "$REPO_DIR/backend/.venv/bin/pip" install -r "$REPO_DIR/backend/requirements.txt"
fi
if [[ "$frontend_deps_changed" == "1" ]]; then
  echo "[$(timestamp)] updating frontend dependencies"
  (cd "$REPO_DIR/frontend" && npm install)
fi

if [[ "$old_head" != "$new_head" || "${FORCE_RESTART:-0}" == "1" ]]; then
  echo "[$(timestamp)] restarting RunPod app processes"
  stop_port_listener "backend" "$BACKEND_PORT"
  start_backend
  wait_for_url "backend /health" "http://127.0.0.1:$BACKEND_PORT/health" 60

  stop_port_listener "frontend" "$FRONTEND_PORT"
  start_frontend
  wait_for_url "frontend /api/health" "http://127.0.0.1:$FRONTEND_PORT/api/health" 90

  stop_port_listener "vl-worker-api" "${VL_WORKER_PORT:-8020}"
  INSTALL_PYTHON_DEPS="$backend_deps_changed" \
    DOCUPARSE_REPO="$REPO_DIR" \
    RUNPOD_WORKDIR="$RUNPOD_WORKDIR" \
    "$REPO_DIR/scripts/runpod-bootstrap-vl-stack.sh"
else
  echo "[$(timestamp)] no Git change; checking running services"
fi

DOCUPARSE_REPO="$REPO_DIR" \
  RUNPOD_WORKDIR="$RUNPOD_WORKDIR" \
  "$REPO_DIR/scripts/runpod-start-reverse-tunnel.sh"
DOCUPARSE_REPO="$REPO_DIR" \
  RUNPOD_WORKDIR="$RUNPOD_WORKDIR" \
  "$REPO_DIR/scripts/runpod-check-reverse-tunnel.sh"

echo "[$(timestamp)] frontend health"
HEALTH_JSON="$(curl -fsS --max-time 30 "http://127.0.0.1:$FRONTEND_PORT/api/health")"
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
    raise SystemExit("frontend health is not ok")
if require_vl_primary:
    if providers.get("primary_reader_available") is not True:
        raise SystemExit("VL primary reader is not available")
    if providers.get("fallback_reason"):
        raise SystemExit(f"backend reports fallback_reason={providers.get('fallback_reason')}")
PY

echo "[$(timestamp)] RunPod deploy finished"
