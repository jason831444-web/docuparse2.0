#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
DOCUPARSE_REPO="${DOCUPARSE_REPO:-$RUNPOD_WORKDIR/docuparse2.0}"
MODEL_DIR="${MODEL_DIR:-/workspace/docuparse_models/paddleocr_vl_1_6_gguf}"
MODEL_FILE="${MODEL_FILE:-PaddleOCR-VL-1.6-GGUF.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-PaddleOCR-VL-1.6-GGUF-mmproj.gguf}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-$RUNPOD_WORKDIR/llama.cpp/build/bin/llama-server}"
VENV_DIR="${VENV_DIR:-$RUNPOD_WORKDIR/worker-venv}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"
UPLOAD_DIR="${UPLOAD_DIR:-$RUNPOD_WORKDIR/uploads}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
VL_WORKER_HOST="${VL_WORKER_HOST:-0.0.0.0}"
VL_WORKER_PORT="${VL_WORKER_PORT:-8020}"
THREADS="${THREADS:-8}"
CTX_SIZE="${CTX_SIZE:-4096}"
N_GPU_LAYERS="${N_GPU_LAYERS:-99}"
N_PREDICT="${N_PREDICT:-512}"

LLAMA_PID_FILE="$LOG_DIR/llama_server.pid"
WORKER_PID_FILE="$LOG_DIR/vl_worker_api.pid"

is_pid_alive() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      echo "ok: $label is reachable"
      return 0
    fi
    sleep 2
  done
  echo "failed: $label did not become reachable at $url" >&2
  return 1
}

mkdir -p "$LOG_DIR" "$UPLOAD_DIR"

if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
  echo "Missing executable llama-server: $LLAMA_SERVER_BIN" >&2
  exit 1
fi
if [[ ! -d "$DOCUPARSE_REPO/backend/app" ]]; then
  echo "Missing Docparse repo/backend: $DOCUPARSE_REPO" >&2
  exit 1
fi
if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "Missing worker venv/uvicorn: $VENV_DIR/bin/uvicorn" >&2
  exit 1
fi
if [[ ! -f "$MODEL_DIR/$MODEL_FILE" ]]; then
  echo "Missing GGUF model: $MODEL_DIR/$MODEL_FILE" >&2
  exit 1
fi
if [[ ! -f "$MODEL_DIR/$MMPROJ_FILE" ]]; then
  echo "Missing GGUF mmproj: $MODEL_DIR/$MMPROJ_FILE" >&2
  exit 1
fi

echo "== GPU =="
nvidia-smi || true

if is_pid_alive "$LLAMA_PID_FILE"; then
  echo "llama-server already running: PID $(cat "$LLAMA_PID_FILE")"
else
  echo "starting llama-server..."
  nohup "$LLAMA_SERVER_BIN" \
    -m "$MODEL_DIR/$MODEL_FILE" \
    --mmproj "$MODEL_DIR/$MMPROJ_FILE" \
    --host "$LLAMA_HOST" \
    --port "$LLAMA_PORT" \
    --temp 0 \
    -t "$THREADS" \
    -c "$CTX_SIZE" \
    -ngl "$N_GPU_LAYERS" \
    > "$LOG_DIR/llama_server_stdout.log" \
    2> "$LOG_DIR/llama_server_stderr.log" &
  echo "$!" > "$LLAMA_PID_FILE"
  echo "llama-server PID $(cat "$LLAMA_PID_FILE")"
fi

wait_for_url "http://$LLAMA_HOST:$LLAMA_PORT/v1/models" "llama-server /v1/models" 90

if is_pid_alive "$WORKER_PID_FILE"; then
  echo "vl-worker-api already running: PID $(cat "$WORKER_PID_FILE")"
else
  echo "starting vl-worker-api..."
  (
    cd "$DOCUPARSE_REPO"
    PYTHONPATH="$DOCUPARSE_REPO/backend" \
    UPLOAD_DIR="$UPLOAD_DIR" \
    PADDLEOCR_VL_GGUF_MODEL_DIR="$MODEL_DIR" \
    PADDLEOCR_VL_GGUF_MODEL_FILE="$MODEL_FILE" \
    PADDLEOCR_VL_GGUF_MMPROJ_FILE="$MMPROJ_FILE" \
    PADDLEOCR_VL_GGUF_SERVER_URL="http://$LLAMA_HOST:$LLAMA_PORT/v1" \
    PADDLEOCR_VL_GGUF_CONCURRENCY=1 \
    PADDLEOCR_VL_GGUF_MAX_PAGES=1 \
    PADDLEOCR_VL_GGUF_N_PREDICT="$N_PREDICT" \
    nohup "$VENV_DIR/bin/uvicorn" \
      app.services.vl_worker_server:app \
      --host "$VL_WORKER_HOST" \
      --port "$VL_WORKER_PORT" \
      > "$LOG_DIR/vl_worker_stdout.log" \
      2> "$LOG_DIR/vl_worker_stderr.log" &
    echo "$!" > "$WORKER_PID_FILE"
  )
  echo "vl-worker-api PID $(cat "$WORKER_PID_FILE")"
fi

wait_for_url "http://127.0.0.1:$VL_WORKER_PORT/health" "vl-worker-api /health" 60

echo
echo "== Worker health =="
curl -fsS "http://127.0.0.1:$VL_WORKER_PORT/health" | python3 -m json.tool

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
    | python3 -m json.tool | tee "$LOG_DIR/analyze_upload_smoke.json"
else
  echo
  echo "Tip: set SMOKE_FILE=/path/to/fixture.pdf to run one /analyze-upload smoke."
fi

echo
echo "Logs: $LOG_DIR"
echo "Uploads: $UPLOAD_DIR"
