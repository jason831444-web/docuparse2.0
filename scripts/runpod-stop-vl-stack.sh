#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"

stop_pid_file() {
  local label="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "skip: no PID file for $label ($pid_file)"
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    echo "stale: empty PID file for $label"
    rm -f "$pid_file"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "stale: $label PID $pid is not running"
    rm -f "$pid_file"
    return 0
  fi
  echo "stopping $label PID $pid"
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "ok: stopped $label"
      return 0
    fi
    sleep 1
  done
  echo "warning: $label PID $pid still running; sending TERM again" >&2
  kill "$pid" 2>/dev/null || true
}

mkdir -p "$LOG_DIR"

stop_pid_file "vl-worker-api" "$LOG_DIR/vl_worker_api.pid"
stop_pid_file "llama-server" "$LOG_DIR/llama_server.pid"

if [[ "${STOP_TUNNEL:-0}" == "1" ]]; then
  stop_pid_file "reverse upload tunnel" "$LOG_DIR/reverse_upload_tunnel.pid"
else
  echo "skip: reverse upload tunnel is left running. Set STOP_TUNNEL=1 to stop its PID file too."
fi

echo
echo "Remaining matching processes:"
ps -ef | grep -E 'llama-server|uvicorn app.services.vl_worker_server|reverse_upload_tunnel|ssh -N' | grep -v grep || true
echo
echo "Models/uploads/logs were not deleted."
