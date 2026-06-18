#!/usr/bin/env bash
set -euo pipefail

RUNPOD_WORKDIR="${RUNPOD_WORKDIR:-/workspace/docuparse-gpu-test}"
LOG_DIR="${LOG_DIR:-$RUNPOD_WORKDIR/logs}"

DO_TUNNEL_USER="${DO_TUNNEL_USER:-root}"
DO_TUNNEL_HOST="${DO_TUNNEL_HOST:-104.236.18.111}"
DO_TUNNEL_KEY="${DO_TUNNEL_KEY:-/root/.ssh/docuparse_do_reverse_tunnel}"
DO_TUNNEL_REMOTE_BIND="${DO_TUNNEL_REMOTE_BIND:-127.0.0.1}"
DO_TUNNEL_REMOTE_PORT="${DO_TUNNEL_REMOTE_PORT:-18020}"
RUNPOD_WORKER_BIND="${RUNPOD_WORKER_BIND:-127.0.0.1}"
RUNPOD_WORKER_PORT="${RUNPOD_WORKER_PORT:-8020}"

PID_FILE="${RUNPOD_REVERSE_TUNNEL_PID_FILE:-$LOG_DIR/reverse_upload_tunnel.pid}"
LOG_FILE="${RUNPOD_REVERSE_TUNNEL_LOG_FILE:-$LOG_DIR/reverse_upload_tunnel.log}"

is_pid_alive() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

mkdir -p "$LOG_DIR"

if [[ ! -f "$DO_TUNNEL_KEY" ]]; then
  echo "Missing DigitalOcean tunnel key: $DO_TUNNEL_KEY" >&2
  exit 1
fi

if ! curl -fsS --max-time 5 "http://$RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT/health" >/dev/null; then
  echo "RunPod vl-worker-api is not reachable at http://$RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT/health" >&2
  echo "Start it first with scripts/runpod-start-vl-stack.sh" >&2
  exit 1
fi

if is_pid_alive; then
  echo "reverse tunnel already running: PID $(cat "$PID_FILE")"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  echo "removing stale reverse tunnel PID file: $PID_FILE"
  rm -f "$PID_FILE"
fi

echo "starting reverse tunnel:"
echo "  DigitalOcean: $DO_TUNNEL_REMOTE_BIND:$DO_TUNNEL_REMOTE_PORT"
echo "  RunPod worker: $RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT"

nohup ssh -N \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=accept-new \
  -i "$DO_TUNNEL_KEY" \
  -R "$DO_TUNNEL_REMOTE_BIND:$DO_TUNNEL_REMOTE_PORT:$RUNPOD_WORKER_BIND:$RUNPOD_WORKER_PORT" \
  "$DO_TUNNEL_USER@$DO_TUNNEL_HOST" \
  > "$LOG_FILE" 2>&1 &

echo "$!" > "$PID_FILE"
sleep 2

if ! is_pid_alive; then
  echo "reverse tunnel failed to stay running. Log:" >&2
  tail -80 "$LOG_FILE" >&2 || true
  exit 1
fi

echo "reverse tunnel PID $(cat "$PID_FILE")"
echo "log: $LOG_FILE"

