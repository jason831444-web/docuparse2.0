#!/usr/bin/env bash
set -euo pipefail

LISTEN_HOST="${DO_RUNPOD_FORWARD_LISTEN_HOST:-172.18.0.1}"
LISTEN_PORT="${DO_RUNPOD_FORWARD_LISTEN_PORT:-18024}"
TARGET_HOST="${DO_RUNPOD_FORWARD_TARGET_HOST:-127.0.0.1}"
TARGET_PORT="${DO_RUNPOD_FORWARD_TARGET_PORT:-18020}"
PID_FILE="${DO_RUNPOD_FORWARD_PID_FILE:-/tmp/docuparse_runpod_tcp_forward.pid}"
SCRIPT_FILE="${DO_RUNPOD_FORWARD_SCRIPT_FILE:-/tmp/docuparse_runpod_tcp_forward.py}"
LOG_FILE="${DO_RUNPOD_FORWARD_LOG_FILE:-/tmp/docuparse_runpod_tcp_forward.log}"

is_pid_alive() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

if is_pid_alive; then
  echo "forwarder already running: PID $(cat "$PID_FILE")"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  echo "removing stale forwarder PID file: $PID_FILE"
  rm -f "$PID_FILE"
fi

cat > "$SCRIPT_FILE" <<PY
import socket
import threading

LISTEN = ("$LISTEN_HOST", int("$LISTEN_PORT"))
TARGET = ("$TARGET_HOST", int("$TARGET_PORT"))

def close_quietly(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass

def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        close_quietly(src)
        close_quietly(dst)

def handle(client):
    try:
        client.settimeout(None)
        upstream = socket.create_connection(TARGET, timeout=10)
        # The connection may stay open for several minutes while RunPod runs
        # PaddleOCR-VL inference. Do not keep the connect timeout as a read
        # timeout, or multipart uploads will fail with an empty reply.
        upstream.settimeout(None)
    except OSError:
        close_quietly(client)
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(LISTEN)
server.listen(64)
print(f"forwarding {LISTEN} -> {TARGET}", flush=True)
while True:
    client, _ = server.accept()
    threading.Thread(target=handle, args=(client,), daemon=True).start()
PY

nohup python3 "$SCRIPT_FILE" > "$LOG_FILE" 2>&1 &
echo "$!" > "$PID_FILE"
sleep 1

if ! is_pid_alive; then
  echo "forwarder failed to stay running. Log:" >&2
  tail -80 "$LOG_FILE" >&2 || true
  exit 1
fi

echo "forwarder PID $(cat "$PID_FILE")"
echo "listen: $LISTEN_HOST:$LISTEN_PORT"
echo "target: $TARGET_HOST:$TARGET_PORT"
echo "log: $LOG_FILE"
