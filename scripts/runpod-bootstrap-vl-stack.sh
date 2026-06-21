#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
DOCUPARSE_REPO="${DOCUPARSE_REPO:-$RUNPOD_WORKDIR/docuparse2.0}"
MODEL_DIR="${MODEL_DIR:-/workspace/docuparse_models/paddleocr_vl_1_6_gguf}"
MODEL_FILE="${MODEL_FILE:-PaddleOCR-VL-1.6-GGUF.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-PaddleOCR-VL-1.6-GGUF-mmproj.gguf}"
VENV_DIR="${VENV_DIR:-$RUNPOD_WORKDIR/worker-venv}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"
ALLOW_MODEL_DOWNLOAD="${ALLOW_MODEL_DOWNLOAD:-0}"
INSTALL_PYTHON_DEPS="${INSTALL_PYTHON_DEPS:-1}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$RUNPOD_WORKDIR/llama.cpp}"
LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}"
ALLOW_LLAMA_CPP_BUILD="${ALLOW_LLAMA_CPP_BUILD:-1}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"
LLAMA_CPP_BUILD_JOBS="${LLAMA_CPP_BUILD_JOBS:-16}"

PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

if [[ ! -d "$DOCUPARSE_REPO/.git" ]]; then
  echo "Missing Docparse git repo: $DOCUPARSE_REPO" >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$MODEL_DIR"

echo "== Sync repo =="
git -C "$DOCUPARSE_REPO" fetch origin main
git -C "$DOCUPARSE_REPO" checkout main
git -C "$DOCUPARSE_REPO" pull --ff-only origin main
git -C "$DOCUPARSE_REPO" rev-parse --short HEAD

echo
echo "== Python worker venv =="
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "creating worker venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
if [[ "$INSTALL_PYTHON_DEPS" == "1" ]]; then
  "$PIP_BIN" install --upgrade pip wheel
  "$PIP_BIN" install -r "$DOCUPARSE_REPO/backend/requirements.txt" -r "$DOCUPARSE_REPO/backend/requirements-ai.txt"
else
  echo "Skipping Python dependency install because INSTALL_PYTHON_DEPS=$INSTALL_PYTHON_DEPS"
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
required = ["fastapi", "uvicorn", "paddleocr", "paddlex", "fitz", "huggingface_hub"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(f"Missing worker Python packages: {', '.join(missing)}")
print("ok: worker Python packages available")
PY

echo
echo "== Model files =="
if [[ -f "$MODEL_DIR/$MODEL_FILE" && -f "$MODEL_DIR/$MMPROJ_FILE" ]]; then
  ls -lh "$MODEL_DIR/$MODEL_FILE" "$MODEL_DIR/$MMPROJ_FILE"
else
  echo "Missing one or more GGUF files in $MODEL_DIR"
  if [[ "$ALLOW_MODEL_DOWNLOAD" != "1" ]]; then
    echo "Set ALLOW_MODEL_DOWNLOAD=1 to download the official PaddlePaddle/PaddleOCR-VL-1.6-GGUF bundle." >&2
    exit 1
  fi
  echo "Downloading official PaddleOCR-VL-1.6-GGUF bundle..."
  PYTHONPATH="$DOCUPARSE_REPO/backend" "$PYTHON_BIN" \
    -m app.scripts.download_paddleocr_vl_gguf \
    --target "$MODEL_DIR" \
    --output-dir "$LOG_DIR/model_download"
fi

echo
echo "== llama.cpp CUDA server =="
if [[ -x "$LLAMA_SERVER_BIN" ]]; then
  "$LLAMA_SERVER_BIN" --version || true
else
  echo "Missing llama-server binary: $LLAMA_SERVER_BIN"
  if [[ "$ALLOW_LLAMA_CPP_BUILD" != "1" ]]; then
    echo "Set ALLOW_LLAMA_CPP_BUILD=1 to clone/build llama.cpp with CUDA support." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$LLAMA_CPP_DIR")"
  if [[ ! -d "$LLAMA_CPP_DIR/.git" ]]; then
    git clone "$LLAMA_CPP_REPO" "$LLAMA_CPP_DIR"
  else
    git -C "$LLAMA_CPP_DIR" pull --ff-only
  fi
  cmake -S "$LLAMA_CPP_DIR" -B "$LLAMA_CPP_DIR/build" \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_COMPILER="${CMAKE_CUDA_COMPILER:-/usr/local/cuda/bin/nvcc}"
  cmake --build "$LLAMA_CPP_DIR/build" -j"$LLAMA_CPP_BUILD_JOBS" --target llama-server
fi
export LLAMA_SERVER_BIN

echo
echo "== Start/check VL stack =="
cd "$DOCUPARSE_REPO"
scripts/runpod-start-vl-stack.sh
scripts/runpod-check-vl-stack.sh

echo
echo "RunPod VL stack bootstrap finished."
echo "Repo: $DOCUPARSE_REPO"
echo "Model: $MODEL_DIR"
echo "Logs: $LOG_DIR"
