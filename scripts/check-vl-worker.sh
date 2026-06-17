#!/usr/bin/env bash
set -euo pipefail

WORKER_URL="${1:-${PADDLEOCR_VL_GGUF_WORKER_URL:-}}"
BACKEND_HEALTH_URL="${BACKEND_HEALTH_URL:-http://localhost:8001/health}"

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

redact_url() {
  python3 - "$1" <<'PY'
from urllib.parse import urlsplit
import sys

raw = sys.argv[1]
parts = urlsplit(raw)
host = parts.hostname or ""
if parts.port:
    host = f"{host}:{parts.port}"
print(f"{parts.scheme}://{host}" if parts.scheme and host else raw)
PY
}

diagnose_unreachable() {
  cat <<'EOF'

Worker health is unreachable. Common causes:
- RunPod pod is stopped or still booting.
- RunPod llama-server is not running.
- RunPod vl-worker-api is not running.
- DigitalOcean tunnel/forward process is down.
- PADDLEOCR_VL_GGUF_WORKER_URL points at the wrong endpoint.
- Backend timed out and is currently using PP-OCRv4 fallback.

Useful checks:
- On RunPod: scripts/runpod-check-vl-stack.sh
- On DigitalOcean: ps -ef | grep -E 'docuparse_runpod|ssh -N|bridge_proxy'
- Backend env: docker compose exec backend sh -lc 'env | grep PADDLEOCR_VL_GGUF'
EOF
}

WORKER_HEALTH_JSON=""
echo "== Worker health: $(redact_url "$WORKER_URL") =="
if WORKER_HEALTH_JSON="$(curl -fsS --max-time 10 "$WORKER_URL/health" 2>/tmp/docparse-worker-health.err)"; then
  python3 -m json.tool <<< "$WORKER_HEALTH_JSON"
else
  cat /tmp/docparse-worker-health.err >&2 || true
  diagnose_unreachable
fi

echo
echo "== Backend health VL block =="
HEALTH_JSON="$(curl -fsS --max-time 10 "$BACKEND_HEALTH_URL" || true)"
python3 -c '
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
providers = payload.get("providers", {})
gguf = providers.get("paddleocr_vl_gguf", {})
summary = {
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
}
print(json.dumps(summary, ensure_ascii=False, indent=2))

warnings = []
if gguf.get("worker_location") != "remote":
    warnings.append("backend health does not report worker_location=remote")
if gguf.get("worker_transport") != "multipart_upload":
    warnings.append("backend health does not report worker_transport=multipart_upload")
if providers.get("primary_reader_available") is not True:
    warnings.append("backend primary_reader_available is not true")
if gguf.get("error"):
    warnings.append(f"backend reports current gguf error: {gguf.get('error')}")
if warnings:
    print("\nOperator warnings:")
    for warning in warnings:
        print(f"- {warning}")
else:
    print("\nRemote RunPod worker appears active: remote + multipart_upload + primary reader available.")
' <<< "$HEALTH_JSON" || true

echo
echo "== DigitalOcean tunnel/forward hints =="
for pidfile in \
  /tmp/docuparse_runpod_tcp_forward.pid \
  /tmp/docuparse_runpod_tunnel/bridge_proxy.pid \
  /tmp/docuparse_runpod_tunnel/reverse_tunnel.pid
do
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "ok: $pidfile -> PID $pid"
    else
      echo "stale: $pidfile -> ${pid:-missing pid}"
    fi
  fi
done

if command -v lsof >/dev/null 2>&1; then
  port="$(python3 - "$WORKER_URL" <<'PY'
from urllib.parse import urlsplit
import sys
parts = urlsplit(sys.argv[1])
print(parts.port or "")
PY
)"
  if [[ -n "$port" ]]; then
    echo
    echo "== Local listener for worker port $port =="
    lsof -nP -iTCP:"$port" -sTCP:LISTEN || true
  fi
fi
