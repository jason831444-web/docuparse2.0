#!/usr/bin/env bash
set -euo pipefail

export HF_HOME="${HF_HOME:-/workspace/.cache/huggingface}"
export PADDLE_PDX_MODEL_SOURCE="${PADDLE_PDX_MODEL_SOURCE:-HUGGINGFACE}"

VLM_HOST="${VLM_HOST:-127.0.0.1}"
VLM_PORT="${VLM_PORT:-8081}"
VLM_BACKEND="${VLM_BACKEND:-vllm}"
VLM_MODEL_NAME="${VLM_MODEL_NAME:-PaddleOCR-VL-1.6-0.9B}"
PIPELINE_CONFIG_SRC="${PIPELINE_CONFIG_SRC:-/home/paddleocr/pipeline_config_vllm.yaml}"
PIPELINE_CONFIG_RUNTIME="${PIPELINE_CONFIG_RUNTIME:-/tmp/pipeline_config_vllm_singlepod.yaml}"
VLM_READY_TIMEOUT_SECONDS="${VLM_READY_TIMEOUT_SECONDS:-600}"

mkdir -p "$HF_HOME" /workspace/.cache/paddlex

echo "[singlepod] starting VLM server on ${VLM_HOST}:${VLM_PORT}"
paddleocr genai_server \
  --model_name "$VLM_MODEL_NAME" \
  --host "$VLM_HOST" \
  --port "$VLM_PORT" \
  --backend "$VLM_BACKEND" &
VLM_PID=$!

cleanup() {
  if kill -0 "$VLM_PID" 2>/dev/null; then
    echo "[singlepod] stopping VLM server pid ${VLM_PID}"
    kill "$VLM_PID" 2>/dev/null || true
    wait "$VLM_PID" 2>/dev/null || true
  fi
}
trap cleanup TERM INT

echo "[singlepod] waiting up to ${VLM_READY_TIMEOUT_SECONDS}s for VLM /v1/models"
deadline=$((SECONDS + VLM_READY_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  if curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" >/tmp/vlm_models.json; then
    echo "[singlepod] VLM server ready"
    break
  fi
  if ! kill -0 "$VLM_PID" 2>/dev/null; then
    echo "[singlepod] VLM server exited before readiness"
    exit 1
  fi
  sleep 2
done

if ! curl -fsS "http://${VLM_HOST}:${VLM_PORT}/v1/models" >/tmp/vlm_models.json; then
  echo "[singlepod] VLM server did not become ready in time"
  exit 1
fi

if [[ ! -f "$PIPELINE_CONFIG_SRC" ]]; then
  echo "[singlepod] missing pipeline config: ${PIPELINE_CONFIG_SRC}"
  exit 1
fi

cp "$PIPELINE_CONFIG_SRC" "$PIPELINE_CONFIG_RUNTIME"
python - "$PIPELINE_CONFIG_RUNTIME" "http://${VLM_HOST}:${VLM_PORT}/v1" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
server_url = sys.argv[2]
text = path.read_text()
text = text.replace("http://paddleocr-vlm-server:8080/v1", server_url)
path.write_text(text)
PY

echo "[singlepod] starting PaddleOCR-VL pipeline server on its default 0.0.0.0:8080"
echo "[singlepod] pipeline config: ${PIPELINE_CONFIG_RUNTIME}"
exec paddlex --serve --pipeline "$PIPELINE_CONFIG_RUNTIME"
