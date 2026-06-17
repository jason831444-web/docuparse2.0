#!/usr/bin/env bash
set -euo pipefail

WORKER_URL="${1:-${PADDLEOCR_VL_GGUF_WORKER_URL:-}}"

if [[ -z "$WORKER_URL" ]]; then
  if [[ -f .env ]]; then
    WORKER_URL="$(grep -E '^PADDLEOCR_VL_GGUF_WORKER_URL=' .env | tail -1 | cut -d= -f2- || true)"
  fi
fi

if [[ -z "$WORKER_URL" ]]; then
  echo "Usage: $0 <worker-url>" >&2
  echo "Or set PADDLEOCR_VL_GGUF_WORKER_URL / run from a repo with .env." >&2
  exit 2
fi

echo "== Worker health: $WORKER_URL =="
curl -fsS "$WORKER_URL/health" | python3 -m json.tool

echo
echo "== Backend health VL block =="
HEALTH_JSON="$(curl -fsS http://localhost:8001/health || true)"
python3 -c '
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
providers = payload.get("providers", {})
gguf = providers.get("paddleocr_vl_gguf", {})
print(json.dumps({
    "backend_status": payload.get("status"),
    "primary_provider": providers.get("primary_provider"),
    "primary_reader_available": providers.get("primary_reader_available"),
    "fallback_provider": providers.get("fallback_provider"),
    "gguf": {
        "status": gguf.get("status"),
        "worker_location": gguf.get("worker_location"),
        "worker_provider": gguf.get("worker_provider"),
        "worker_url_host": gguf.get("worker_url_host"),
        "worker_transport": gguf.get("worker_transport"),
        "error": gguf.get("error"),
    },
}, ensure_ascii=False, indent=2))
' <<< "$HEALTH_JSON" || true
