#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
DOCUPARSE_REPO="${DOCUPARSE_REPO:-$RUNPOD_WORKDIR/docuparse2.0}"
MODEL_DIR="${MODEL_DIR:-/workspace/docuparse_models/paddleocr_vl_1_6_gguf}"
MODEL_FILE="${MODEL_FILE:-PaddleOCR-VL-1.6-GGUF.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-PaddleOCR-VL-1.6-GGUF-mmproj.gguf}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
VL_WORKER_PORT="${VL_WORKER_PORT:-8020}"

check_pid() {
  local label="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "missing: $label PID file ($pid_file)"
    return 1
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    echo "stale: $label PID file is empty"
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "ok: $label PID $pid"
    return 0
  fi
  echo "stale: $label PID $pid is not running"
  return 1
}

print_json_or_raw() {
  python3 -m json.tool 2>/dev/null || cat
}

echo "== RunPod GPU =="
nvidia-smi || true

echo
echo "== Repo/model files =="
if [[ -d "$DOCUPARSE_REPO/.git" ]]; then
  git -C "$DOCUPARSE_REPO" rev-parse --short HEAD || true
  git -C "$DOCUPARSE_REPO" status --short || true
else
  echo "missing repo: $DOCUPARSE_REPO"
fi
ls -lh "$MODEL_DIR/$MODEL_FILE" "$MODEL_DIR/$MMPROJ_FILE" 2>/dev/null || true

echo
echo "== PID files =="
check_pid "llama-server" "$LOG_DIR/llama_server.pid" || true
check_pid "vl-worker-api" "$LOG_DIR/vl_worker_api.pid" || true
check_pid "reverse upload tunnel" "$LOG_DIR/reverse_upload_tunnel.pid" || true

echo
echo "== llama-server models =="
if curl -fsS --max-time 10 "http://127.0.0.1:$LLAMA_PORT/v1/models" | print_json_or_raw; then
  true
else
  echo "failed: llama-server /v1/models is not reachable"
fi

echo
echo "== vl-worker-api health =="
WORKER_HEALTH=""
if WORKER_HEALTH="$(curl -fsS --max-time 10 "http://127.0.0.1:$VL_WORKER_PORT/health")"; then
  python3 -m json.tool <<< "$WORKER_HEALTH"
  python3 - "$WORKER_HEALTH" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
last_error = payload.get("last_error")
last_success = payload.get("last_success_at")
last_request = payload.get("last_request_at")
if last_error and last_success:
    print("\nNote: last_error can be stale if last_success_at is newer than the failed request.")
elif last_error:
    print("\nCurrent warning: worker reports last_error and no later success is visible.")
print(f"last_request_at={last_request}")
print(f"last_success_at={last_success}")
PY
else
  echo "failed: vl-worker-api /health is not reachable"
fi

if [[ -n "${SMOKE_FILE:-}" ]]; then
  echo
  echo "== Analyze-upload smoke: $SMOKE_FILE =="
  if [[ ! -f "$SMOKE_FILE" ]]; then
    echo "SMOKE_FILE does not exist: $SMOKE_FILE" >&2
    exit 1
  fi
  curl -fsS --max-time "${SMOKE_TIMEOUT_SECONDS:-900}" \
    -F "file=@$SMOKE_FILE" \
    -F "original_filename=$(basename "$SMOKE_FILE")" \
    "http://127.0.0.1:$VL_WORKER_PORT/analyze-upload" \
    | tee "$LOG_DIR/analyze_upload_check_smoke.json" \
    | python3 -m json.tool
else
  echo
  echo "Tip: set SMOKE_FILE=/path/to/fixture.pdf to run one /analyze-upload inference smoke."
fi

echo
echo "== Recent logs =="
for log in \
  "$LOG_DIR/vl_worker_stderr.log" \
  "$LOG_DIR/vl_worker_stdout.log" \
  "$LOG_DIR/llama_server_stderr.log"
do
  if [[ -f "$log" ]]; then
    echo "--- $log"
    tail -40 "$log" || true
  fi
done
