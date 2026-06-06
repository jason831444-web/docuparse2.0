#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKEND_HEALTH_URL="${BACKEND_HEALTH_URL:-http://localhost:8001/health}"
OCR_WORKER_HEALTH_URL="${OCR_WORKER_HEALTH_URL:-http://localhost:8010/health}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8001/api}"
SAMPLE_PDF="${SAMPLE_PDF:-samples/pdf_samples/docuparse_image_based_pdf_samples_10/01_image_po_clean_korean.pdf}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run() {
  log "$*"
  "$@"
}

print_failure_diagnostics() {
  log "Failure diagnostics"
  docker compose ps || true
  printf '\n--- ocr-worker logs ---\n'
  docker compose logs --tail=200 ocr-worker || true
  printf '\n--- backend logs ---\n'
  docker compose logs --tail=200 backend || true
  printf '\n--- classified failure hints ---\n'
  local logs
  logs="$(docker compose logs --tail=300 ocr-worker backend 2>/dev/null || true)"
  if grep -qiE 'ConvertPirAttribute2RuntimeAttribute|onednn_instruction|FLAGS_use_onednn|FLAGS_enable_pir_api|PADDLE_RUNTIME|Unimplemented.*pir::' <<<"$logs"; then
    echo "classification=PADDLE_RUNTIME_INFERENCE_ERROR"
  elif grep -qiE 'Segmentation fault|SIGSEGV|returncode -11|FatalError' <<<"$logs"; then
    echo "classification=SIGSEGV_NATIVE_RUNTIME_CRASH"
  elif grep -qiE 'timeout|timed out' <<<"$logs"; then
    echo "classification=TIMEOUT"
  elif grep -qiE 'ModuleNotFoundError|ImportError|No module named' <<<"$logs"; then
    echo "classification=DEPENDENCY_ERROR"
  elif grep -qiE 'download|Fetching|connectivity|model files' <<<"$logs"; then
    echo "classification=MODEL_DOWNLOAD_OR_CACHE_EVENT"
  else
    echo "classification=UNKNOWN_CHECK_LOGS"
  fi
}

trap 'status=$?; if [ "$status" -ne 0 ]; then print_failure_diagnostics; fi' EXIT

log "Host architecture"
uname -a
uname -m

log "Docker architecture"
docker version --format '{{json .Server}}' || docker version
docker compose version

if [ "$(uname -m)" != "x86_64" ] && [ "$(uname -m)" != "amd64" ]; then
  log "WARNING: this host is not Linux x86_64/amd64. PaddleOCR may behave differently here."
fi

if [ ! -f "$SAMPLE_PDF" ]; then
  echo "Sample PDF not found: $SAMPLE_PDF" >&2
  exit 1
fi

run docker compose down -v
run docker compose up -d --build db ocr-worker backend frontend
log "Waiting for services"
sleep 15
run docker compose ps

log "Backend health"
curl -fsS "$BACKEND_HEALTH_URL"
echo

log "OCR worker health"
curl -fsS "$OCR_WORKER_HEALTH_URL"
echo

log "OCR worker smoke test"
if docker compose exec -T backend python -m app.scripts.ocr_worker_smoke_test; then
  echo "smoke_test=PASS"
else
  echo "smoke_test=FAIL"
  print_failure_diagnostics
fi

log "Upload image-only PDF sample"
upload_output="$(curl -fsS -w $'\nHTTP %{http_code}\n' -F "file=@${SAMPLE_PDF};type=application/pdf" "${API_BASE_URL}/documents/upload")"
printf '%s\n' "$upload_output"
document_id="$(UPLOAD_OUTPUT="$upload_output" python3 - <<'PY'
import json, os
text = os.environ["UPLOAD_OUTPUT"].split("\nHTTP ", 1)[0]
print(json.loads(text)["id"])
PY
)"
echo "document_id=${document_id}"

log "Latest OCR metadata"
docker compose exec -T backend env DOCUPARSE_API_BASE=http://localhost:8000/api DOCUPARSE_DOCUMENT_ID="$document_id" \
  python -m app.scripts.print_latest_ocr_metadata --wait --timeout "$WAIT_TIMEOUT_SECONDS"

log "Provider success assertion"
metadata_json="$(docker compose exec -T backend env DOCUPARSE_API_BASE=http://localhost:8000/api DOCUPARSE_DOCUMENT_ID="$document_id" \
  python -m app.scripts.print_latest_ocr_metadata --wait --timeout 5)"
provider="$(METADATA_JSON="$metadata_json" python3 - <<'PY'
import json, os
print(json.loads(os.environ["METADATA_JSON"]).get("ocr_provider_succeeded"))
PY
)"
if [ "$provider" = "ocr_worker_paddleocr" ]; then
  echo "provider_succeeded=ocr_worker_paddleocr"
  echo "linux_x86_ocr_worker_validation=PASS"
else
  echo "provider_succeeded=${provider}"
  echo "linux_x86_ocr_worker_validation=FALLBACK_OR_FAIL"
  print_failure_diagnostics
fi

trap - EXIT
