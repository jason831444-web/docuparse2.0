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

Start `llama-server` on the RunPod pod:

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
