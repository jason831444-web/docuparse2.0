#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"
PID_FILE="${RUNPOD_REVERSE_TUNNEL_PID_FILE:-$LOG_DIR/reverse_upload_tunnel.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "skip: no reverse tunnel PID file ($PID_FILE)"
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$pid" ]]; then
  echo "stale: empty reverse tunnel PID file"
  rm -f "$PID_FILE"
  exit 0
fi

if ! kill -0 "$pid" 2>/dev/null; then
  echo "stale: reverse tunnel PID $pid is not running"
  rm -f "$PID_FILE"
  exit 0
fi

echo "stopping reverse tunnel PID $pid"
kill "$pid" 2>/dev/null || true
for _ in {1..20}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "ok: stopped reverse tunnel"
    exit 0
  fi
  sleep 1
done

echo "warning: reverse tunnel PID $pid still running after TERM" >&2
echo "not using pkill; inspect the process manually before forcing termination" >&2
exit 1

