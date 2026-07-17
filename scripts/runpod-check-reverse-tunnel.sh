#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"

DO_TUNNEL_USER="${DO_TUNNEL_USER:-root}"
DO_TUNNEL_HOST="${DO_TUNNEL_HOST:-162.243.251.204}"
DO_TUNNEL_KEY="${DO_TUNNEL_KEY:-/root/.ssh/docuparse_do_reverse_tunnel}"
DO_TUNNEL_REMOTE_BIND="${DO_TUNNEL_REMOTE_BIND:-127.0.0.1}"
DO_TUNNEL_REMOTE_PORT="${DO_TUNNEL_REMOTE_PORT:-18020}"
RUNPOD_WORKER_BIND="${RUNPOD_WORKER_BIND:-127.0.0.1}"
RUNPOD_WORKER_PORT="${RUNPOD_WORKER_PORT:-8020}"

PID_FILE="${RUNPOD_REVERSE_TUNNEL_PID_FILE:-$LOG_DIR/reverse_upload_tunnel.pid}"
LOG_FILE="${RUNPOD_REVERSE_TUNNEL_LOG_FILE:-$LOG_DIR/reverse_upload_tunnel.log}"

echo "== RunPod local worker health =="
if curl -fsS --max-time 5 "http://$RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT/health" | python3 -m json.tool; then
  true
else
  echo "failed: local vl-worker-api is not reachable at $RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT"
fi

echo
echo "== Reverse tunnel PID =="
if [[ ! -f "$PID_FILE" ]]; then
  echo "missing: $PID_FILE"
else
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "ok: reverse tunnel PID $pid"
  else
    echo "stale: reverse tunnel PID file exists but process is not running"
  fi
fi

echo
echo "== DigitalOcean remote forwarded health =="
if [[ -f "$DO_TUNNEL_KEY" ]]; then
  if ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=8 \
    -o StrictHostKeyChecking=accept-new \
    -i "$DO_TUNNEL_KEY" \
    "$DO_TUNNEL_USER@$DO_TUNNEL_HOST" \
    "curl -fsS --max-time 5 http://$DO_TUNNEL_REMOTE_BIND:$DO_TUNNEL_REMOTE_PORT/health" \
    | python3 -m json.tool; then
    true
  else
    echo "failed: DigitalOcean cannot reach reverse tunnel at $DO_TUNNEL_REMOTE_BIND:$DO_TUNNEL_REMOTE_PORT"
  fi
else
  echo "skip: missing DigitalOcean tunnel key: $DO_TUNNEL_KEY"
fi

echo
echo "== Recent reverse tunnel log =="
if [[ -f "$LOG_FILE" ]]; then
  tail -80 "$LOG_FILE" || true
else
  echo "missing: $LOG_FILE"
fi
