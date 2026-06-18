# RunPod Remote VL Worker

This guide keeps the DigitalOcean app server as the Docparse control plane while
using a RunPod GPU pod only for PaddleOCR-VL GGUF inference.

## Runtime Shape

```text
Browser upload
-> DigitalOcean backend
-> multipart POST /analyze-upload
-> RunPod vl-worker-api
-> RunPod llama-server + PaddleOCR-VL-1.6-GGUF
-> DigitalOcean parser / validation gate
-> confirmed business data or review candidates
```

The remote worker is a reader only. Confirmed values still pass the
VLCandidateParser, ValidationGate, no-price guardrails, hidden-column guardrails,
and PP-OCRv4 fallback policy.

## RunPod Processes

The preferred operational path is to use the repo scripts from the RunPod pod:

```bash
cd /workspace/docuparse-gpu-test/docuparse2.0
scripts/runpod-bootstrap-vl-stack.sh
```

`runpod-bootstrap-vl-stack.sh` is the closest supported "git pull and recover"
path for a restarted pod. It:

- fetches and fast-forwards `main`;
- creates `/workspace/docuparse-gpu-test/worker-venv` when it is missing;
- installs the backend and PaddleOCR-VL worker dependencies into that venv;
- verifies the official GGUF model and mmproj files;
- optionally downloads the official PaddlePaddle bundle only when
  `ALLOW_MODEL_DOWNLOAD=1` is set;
- verifies `llama-server`, and can clone/build CUDA `llama.cpp` when the binary
  is missing;
- starts `llama-server` and `vl-worker-api` without duplicating existing PIDs;
- runs the RunPod health check.

That means the normal recovery command after a RunPod restart is:

```bash
cd /workspace/docuparse-gpu-test/docuparse2.0
git pull --ff-only origin main
scripts/runpod-bootstrap-vl-stack.sh
```

If the model directory is empty and you intentionally want the pod to download
the official model bundle, run:

```bash
ALLOW_MODEL_DOWNLOAD=1 scripts/runpod-bootstrap-vl-stack.sh
```

The script never deletes models, logs, uploads, database volumes, or server
files. It fails instead of using community GGUF artifacts.

For manual checks after the stack is already running:

```bash
scripts/runpod-start-vl-stack.sh
scripts/runpod-check-vl-stack.sh
```

The start script is idempotent: if `llama-server` or `vl-worker-api` is already
running from the recorded PID files, it reports the existing process instead of
starting a duplicate.

It expects these default paths:

- repo: `/workspace/docuparse-gpu-test/docuparse2.0`
- logs: `/workspace/docuparse-gpu-test/logs`
- uploads: `/workspace/docuparse-gpu-test/uploads`
- model dir: `/workspace/docuparse_models/paddleocr_vl_1_6_gguf`
- worker venv: `/workspace/docuparse-gpu-test/worker-venv`
- llama-server: `/opt/llama.cpp/build/bin/llama-server`

You can override paths with environment variables such as `MODEL_DIR`,
`DOCUPARSE_REPO`, `VENV_DIR`, `LLAMA_SERVER_BIN`, `LLAMA_CPP_DIR`, `LOG_DIR`,
and `UPLOAD_DIR`. Set `INSTALL_PYTHON_DEPS=0` when the venv is already prepared
and you only want to start/check processes. Set `ALLOW_LLAMA_CPP_BUILD=0` if you
want bootstrap to fail rather than clone/build llama.cpp when the server binary
is missing.

To run a one-file inference smoke from the RunPod pod, pass `SMOKE_FILE`:

```bash
SMOKE_FILE=/workspace/docuparse-gpu-test/docuparse2.0/samples/pdf_samples/manufacturing_regression_v1/files/MFG-001_purchase_order_uncropped.pdf \
  scripts/runpod-check-vl-stack.sh
```

Manual command reference for `llama-server`:

```bash
/opt/llama.cpp/build/bin/llama-server \
  -m /workspace/docuparse_models/paddleocr_vl_1_6_gguf/PaddleOCR-VL-1.6-GGUF.gguf \
  --mmproj /workspace/docuparse_models/paddleocr_vl_1_6_gguf/PaddleOCR-VL-1.6-GGUF-mmproj.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  --temp 0 \
  -t 8 \
  -c 4096 \
  -ngl 99
```

Start the VL worker API on the RunPod pod:

```bash
cd /workspace/docuparse-gpu-test/docuparse2.0
PYTHONPATH=/workspace/docuparse-gpu-test/docuparse2.0/backend \
UPLOAD_DIR=/workspace/docuparse-gpu-test/uploads \
PADDLEOCR_VL_GGUF_MODEL_DIR=/workspace/docuparse_models/paddleocr_vl_1_6_gguf \
PADDLEOCR_VL_GGUF_SERVER_URL=http://127.0.0.1:8080/v1 \
PADDLEOCR_VL_GGUF_CONCURRENCY=1 \
PADDLEOCR_VL_GGUF_MAX_PAGES=1 \
PADDLEOCR_VL_GGUF_N_PREDICT=512 \
/workspace/docuparse-gpu-test/worker-venv/bin/uvicorn \
  app.services.vl_worker_server:app \
  --host 0.0.0.0 \
  --port 8020
```

Check the worker:

```bash
curl -s http://127.0.0.1:8020/health | python3 -m json.tool
```

Stop only the RunPod-side worker stack with PID files:

```bash
scripts/runpod-stop-vl-stack.sh
```

The stop script does not delete models, uploads, logs, or Docker volumes. It does
not stop or terminate the RunPod pod. By default it leaves any reverse tunnel
alone; set `STOP_TUNNEL=1` only when you intentionally want to stop the tunnel
PID recorded in `/workspace/docuparse-gpu-test/logs/reverse_upload_tunnel.pid`.

## RunPod Reverse Tunnel

When the RunPod pod cannot expose `vl-worker-api` directly, keep the
DigitalOcean-visible endpoint stable by opening a reverse SSH tunnel from
RunPod back to DigitalOcean:

```text
RunPod 127.0.0.1:8020 -> DigitalOcean 127.0.0.1:18020
```

Use the repo scripts from inside the RunPod pod:

```bash
cd /workspace/docuparse-gpu-test/docuparse2.0
scripts/runpod-start-reverse-tunnel.sh
scripts/runpod-check-reverse-tunnel.sh
```

Defaults:

- DigitalOcean SSH host: `104.236.18.111`
- DigitalOcean SSH user: `root`
- DigitalOcean tunnel key: `/root/.ssh/docuparse_do_reverse_tunnel`
- DigitalOcean bind: `127.0.0.1:18020`
- RunPod worker target: `127.0.0.1:8020`
- PID file: `/workspace/docuparse-gpu-test/logs/reverse_upload_tunnel.pid`
- log file: `/workspace/docuparse-gpu-test/logs/reverse_upload_tunnel.log`

The start script first checks the local RunPod worker `/health`, refuses to run
without the tunnel key, and avoids duplicate SSH tunnels when the PID file points
to a live process. Override values with environment variables such as
`DO_TUNNEL_HOST`, `DO_TUNNEL_KEY`, `DO_TUNNEL_REMOTE_PORT`,
`RUNPOD_WORKER_PORT`, `RUNPOD_REVERSE_TUNNEL_PID_FILE`, and
`RUNPOD_REVERSE_TUNNEL_LOG_FILE`.

Stop only the tunnel process recorded in the PID file:

```bash
scripts/runpod-stop-reverse-tunnel.sh
```

The stop script does not use `pkill -f`, does not stop `llama-server`, does not
stop `vl-worker-api`, and never deletes models, uploads, or logs.

After the reverse tunnel is running, verify the full DigitalOcean-facing path:

```bash
ssh docuparse-server
cd /root/docuparse2.0
scripts/check-vl-worker.sh http://172.18.0.1:18024
```

The backend-visible URL remains `http://172.18.0.1:18024`; the reverse tunnel is
only one leg of that path.

## DigitalOcean Connection

Expose the RunPod worker to the DigitalOcean backend with a network tunnel or
RunPod HTTP/TCP endpoint. The current tested setup uses a host-side TCP forward
from the Docker bridge gateway to the RunPod worker.

Set the backend worker URL to the reachable endpoint:

```bash
scripts/use-runpod-vl-worker.sh http://172.18.0.1:18024 1200
```

The script:

- backs up `.env`;
- sets `PADDLEOCR_VL_GGUF_WORKER_URL`;
- sets `PADDLEOCR_VL_GGUF_TIMEOUT_SECONDS`;
- restarts only the backend container;
- prints the VL health block.

It does not touch database volumes, uploaded files, model files, or RunPod pod
lifecycle.

Check the active worker:

```bash
scripts/check-vl-worker.sh
```

Expected active remote state:

```json
{
  "status": "remote_primary_reader_candidate",
  "worker_location": "remote",
  "worker_provider": "remote_vl_worker",
  "worker_transport": "multipart_upload"
}
```

The check script prints:

- remote worker `/health`;
- backend `/health` VL block;
- whether backend reports `worker_location=remote`;
- whether backend reports `worker_transport=multipart_upload`;
- whether the primary reader is available;
- likely causes when the worker URL is unreachable;
- DigitalOcean tunnel/forward PID hints when PID files are present.

## Roll Back To Local CPU Worker

```bash
scripts/use-local-vl-worker.sh 240
```

If a manual TCP forward was started on DigitalOcean, stop only that process:

```bash
kill "$(cat /tmp/docuparse_runpod_tcp_forward.pid)"
```

Do not run `docker compose down -v`. Do not remove database, upload, model, or
PaddleOCR cache volumes.

## Regression Checks

After switching to RunPod, run:

```bash
docker compose exec backend sh -lc '
  PYTHONPATH=. DOCUPARSE_DELETE_AFTER_DUMP=1 \
  python -m app.scripts.run_generated_vl_primary_regression \
    --sample-dir /tmp/regression_samples/manufacturing_regression_v1/files \
    --output-dir /tmp/docuparse_e2e_logs/runpod_manufacturing \
    --api-base http://localhost:8000/api \
    --timeout-seconds 1200 \
    --cooldown-seconds 0.5 \
    --progress
'
```

Keep these invariants:

- FAIL 0;
- dangerous contamination 0;
- hidden/cropped amount fields are not confirmed;
- no-price documents do not create amounts;
- PP-OCRv4 fallback remains available.

## Troubleshooting

- `vl_worker_unreachable`: check the RunPod pod, tunnel, and
  `PADDLEOCR_VL_GGUF_WORKER_URL`.
- `/health` works but uploads fail: verify `/analyze-upload`, upload directory
  write permissions, and backend timeout.
- stale `last_error`: the worker health can retain an older error even after
  later successful requests; confirm with `/analyze-upload` logs.
- backend still shows local CPU: rebuild/restart backend and verify the env
  inside the container.
- RunPod cost continues until the pod is stopped or terminated in RunPod.

Stop or terminate the RunPod pod only after confirming the DigitalOcean backend
has been rolled back to local worker or fallback-only operation.

## Pod Start Lifecycle

When a stopped RunPod pod is started again, the network endpoint may come back
before all processes are ready. Do this sequence:

1. SSH into RunPod.
2. Check GPU availability with `nvidia-smi`.
3. Go to the repo: `cd /workspace/docuparse-gpu-test/docuparse2.0`.
4. Confirm the official model files exist:
   `ls -lh /workspace/docuparse_models/paddleocr_vl_1_6_gguf`.
5. Sync code: `git pull --ff-only origin main`.
6. Run `scripts/runpod-bootstrap-vl-stack.sh` to combine git sync, model
   verification, process start, and health checks.
7. Or run `scripts/runpod-start-vl-stack.sh` followed by
   `scripts/runpod-check-vl-stack.sh` when dependencies are already prepared.
8. Start the reverse tunnel:
   `scripts/runpod-start-reverse-tunnel.sh`.
9. Check the reverse tunnel:
   `scripts/runpod-check-reverse-tunnel.sh`.
10. On DigitalOcean, run
   `scripts/check-vl-worker.sh http://172.18.0.1:18024`.
11. Upload one fixture through the normal UI/API path, for example
   `MFG-003_delivery_no_price_uncropped.jpg`.
12. Confirm RunPod logs show `POST /analyze-upload 200 OK`.
13. Confirm document metadata includes `worker_transport=multipart_upload` and
   `worker_location=remote`.

RunPod start does not automatically guarantee that DigitalOcean is connected
because four independent pieces must be alive: the pod, `llama-server`,
`vl-worker-api`, and the DigitalOcean reachable endpoint/tunnel.

## Startup Command Candidate

Do not apply this automatically without an operator review, but the RunPod
template/start command can be configured with a variant of:

```bash
set -euo pipefail
cd /workspace/docuparse-gpu-test/docuparse2.0
git pull --ff-only origin main
scripts/runpod-bootstrap-vl-stack.sh
```

If a reverse tunnel is required, start it after the worker stack and record its
PID in `/workspace/docuparse-gpu-test/logs/reverse_upload_tunnel.pid`. Keep
tunnel credentials out of git.

## Before Stopping RunPod

RunPod billing continues until the pod is stopped or terminated. Before stopping
it, choose one of these operational states:

- keep DigitalOcean pointed at RunPod and accept that uploads will fall back if
  the pod is down;
- switch DigitalOcean back to the local CPU worker with
  `scripts/use-local-vl-worker.sh`;
- pause uploads while the remote worker is unavailable.

Never stop the pod and assume existing tunnels or backend health will remain
valid after restart. Re-run both RunPod and DigitalOcean check scripts.
