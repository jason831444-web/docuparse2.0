#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "Refusing to run PaddleOCR-VL inference on macOS. Use the Linux server." >&2
  exit 2
fi

REPO_ROOT="${REPO_ROOT:-$(pwd)}"
SAMPLE="${SAMPLE:-samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf}"
MODEL_DIR="${PADDLEOCR_VL_GGUF_MODEL_DIR:-/root/docuparse_models/paddleocr_vl_1_6_gguf}"
MODEL_FILE="${PADDLEOCR_VL_GGUF_MODEL_FILE:-PaddleOCR-VL-1.6-GGUF.gguf}"
MMPROJ_FILE="${PADDLEOCR_VL_GGUF_MMPROJ_FILE:-PaddleOCR-VL-1.6-GGUF-mmproj.gguf}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/opt/llama.cpp/build/bin/llama-server}"
PYTHON_BIN="${PYTHON_BIN:-/tmp/docuparse_vl_smoke_venv/bin/python}"
HOST="${PADDLEOCR_VL_GGUF_HOST:-127.0.0.1}"
PORT="${PADDLEOCR_VL_GGUF_PORT:-8081}"
THREADS="${PADDLEOCR_VL_GGUF_THREADS:-2}"
CONTEXT_SIZE="${PADDLEOCR_VL_GGUF_CONTEXT_SIZE:-4096}"
TIMEOUT_SECONDS="${PADDLEOCR_VL_GGUF_TIMEOUT_SECONDS:-600}"
RENDER_SCALE="${PADDLEOCR_VL_GGUF_RENDER_SCALE:-2.0}"
STOP_COMPOSE_VL_WORKER="${STOP_COMPOSE_VL_WORKER:-true}"
RESTORE_COMPOSE_VL_WORKER="${RESTORE_COMPOSE_VL_WORKER:-true}"
MANUAL_VISUAL_CHECK_FILE="${MANUAL_VISUAL_CHECK_FILE:-}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/server_$(date +%Y%m%d_%H%M%S)}"

MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"
MMPROJ_PATH="${MODEL_DIR}/${MMPROJ_FILE}"

if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "REPO_ROOT must point to the DocuParse git checkout: $REPO_ROOT" >&2
  exit 2
fi
if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
  echo "llama-server binary missing: $LLAMA_SERVER_BIN" >&2
  exit 2
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python smoke venv missing: $PYTHON_BIN" >&2
  exit 2
fi
if [[ ! -f "$MODEL_PATH" || ! -f "$MMPROJ_PATH" ]]; then
  echo "GGUF model files missing under $MODEL_DIR" >&2
  exit 2
fi
if [[ ! -f "$REPO_ROOT/$SAMPLE" ]]; then
  echo "Sample PDF missing: $REPO_ROOT/$SAMPLE" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"
LLAMA_PID_FILE="$OUTPUT_DIR/llama_server.pid"
MONITOR_PID_FILE="$OUTPUT_DIR/resource_monitor.pid"

cleanup() {
  if [[ -f "$MONITOR_PID_FILE" ]]; then
    kill "$(cat "$MONITOR_PID_FILE")" 2>/dev/null || true
  fi
  if [[ -f "$LLAMA_PID_FILE" ]]; then
    kill "$(cat "$LLAMA_PID_FILE")" 2>/dev/null || true
  fi
  if [[ "$STOP_COMPOSE_VL_WORKER" == "true" && "$RESTORE_COMPOSE_VL_WORKER" == "true" ]]; then
    (
      cd "$REPO_ROOT"
      PADDLEOCR_VL_GGUF_HOST_MODEL_DIR="$MODEL_DIR" docker compose --profile vl up -d vl-worker-gguf >/dev/null 2>&1 || true
    )
  fi
}
trap cleanup EXIT

cd "$REPO_ROOT"

echo "server_head=$(git rev-parse --short HEAD)" | tee "$OUTPUT_DIR/run_context.txt"
echo "sample=$SAMPLE" | tee -a "$OUTPUT_DIR/run_context.txt"
echo "model_dir=$MODEL_DIR" | tee -a "$OUTPUT_DIR/run_context.txt"
echo "output_dir=$OUTPUT_DIR" | tee -a "$OUTPUT_DIR/run_context.txt"

if [[ "$STOP_COMPOSE_VL_WORKER" == "true" ]]; then
  docker compose stop vl-worker-gguf >/dev/null 2>&1 || true
fi

"$LLAMA_SERVER_BIN" \
  -m "$MODEL_PATH" \
  --mmproj "$MMPROJ_PATH" \
  --host "$HOST" \
  --port "$PORT" \
  --temp 0 \
  -t "$THREADS" \
  -c "$CONTEXT_SIZE" \
  > "$OUTPUT_DIR/llama_server_stdout.log" \
  2> "$OUTPUT_DIR/llama_server_stderr.log" &
echo $! > "$LLAMA_PID_FILE"

sleep 12
curl -s --max-time 5 "http://${HOST}:${PORT}/health" > "$OUTPUT_DIR/llama_health.json" || true
curl -s --max-time 5 "http://${HOST}:${PORT}/v1/models" > "$OUTPUT_DIR/llama_models.json" || true

(
  while true; do
    date -Is
    free -h
    ps aux --sort=-%mem | head -12
    echo "---"
    sleep 5
  done
) > "$OUTPUT_DIR/resource_monitor.log" 2>&1 &
echo $! > "$MONITOR_PID_FILE"

SMOKE_ARGS=(
  -m app.scripts.smoke_paddleocr_vl_gguf
  --sample "$SAMPLE"
  --output-dir "$OUTPUT_DIR"
  --render-scale "$RENDER_SCALE"
)
if [[ -n "$MANUAL_VISUAL_CHECK_FILE" ]]; then
  SMOKE_ARGS+=(--manual-visual-check-file "$MANUAL_VISUAL_CHECK_FILE")
fi

set +e
PYTHONPATH=backend \
PADDLEOCR_VL_GGUF_MODEL_DIR="$MODEL_DIR" \
PADDLEOCR_VL_GGUF_SERVER_URL="http://${HOST}:${PORT}/v1" \
PADDLEOCR_VL_GGUF_CONCURRENCY=1 \
timeout "${TIMEOUT_SECONDS}s" "$PYTHON_BIN" "${SMOKE_ARGS[@]}" \
  > "$OUTPUT_DIR/stdout.log" \
  2> "$OUTPUT_DIR/stderr.log"
CODE=$?
set -e

echo "exit_code=$CODE" | tee -a "$OUTPUT_DIR/run_context.txt"
echo "output_dir=$OUTPUT_DIR"
tail -160 "$OUTPUT_DIR/stdout.log" || true
tail -160 "$OUTPUT_DIR/stderr.log" || true
exit "$CODE"
