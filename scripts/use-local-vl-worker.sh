#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
LOCAL_WORKER_URL="${LOCAL_WORKER_URL:-http://vl-worker-api:8020}"
TIMEOUT_SECONDS="${1:-240}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Run this from the Docparse server repo root." >&2
  exit 1
fi

BACKUP="${ENV_FILE}.backup-local-vl-$(date +%Y%m%d%H%M%S)"
cp "$ENV_FILE" "$BACKUP"

python3 - "$ENV_FILE" "$LOCAL_WORKER_URL" "$TIMEOUT_SECONDS" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
worker_url = sys.argv[2]
timeout = sys.argv[3]
updates = {
    "PADDLEOCR_VL_GGUF_WORKER_URL": worker_url,
    "PADDLEOCR_VL_GGUF_TIMEOUT_SECONDS": timeout,
}
lines = path.read_text().splitlines()
seen = set()
out = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY

echo "Backed up $ENV_FILE to $BACKUP"
echo "Restored local VL worker: $LOCAL_WORKER_URL"
docker compose up -d backend
echo
echo "Backend health VL block:"
HEALTH_JSON="$(curl -fsS http://localhost:8001/health || true)"
python3 -c '
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
gguf = payload.get("providers", {}).get("paddleocr_vl_gguf", {})
print(json.dumps({
    "status": gguf.get("status"),
    "worker_location": gguf.get("worker_location"),
    "worker_provider": gguf.get("worker_provider"),
    "worker_url_host": gguf.get("worker_url_host"),
    "worker_transport": gguf.get("worker_transport"),
    "primary_reader_available": gguf.get("primary_reader_available"),
    "error": gguf.get("error"),
}, ensure_ascii=False, indent=2))
' <<< "$HEALTH_JSON" || true
