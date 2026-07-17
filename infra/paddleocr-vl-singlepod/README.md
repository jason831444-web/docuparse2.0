# PaddleOCR-VL Single-Pod PoC

This folder contains an experimental single-container image design for testing
the official PaddleOCR-VL page-level parser on RunPod without touching the
DocuParse production GPU worker.

## Why This Exists

The official PaddleOCR-VL deployment is a two-container shape:

- `paddleocr-vlm-server`: VLM recognition server, usually vLLM-backed.
- `paddleocr-vl-api`: page-level parser API that produces `parsing_res_list`,
  table blocks, markdown, and structured output.

RunPod Pods are often easiest to operate as one custom image, so this PoC starts
both processes in one container:

```text
external request -> pipeline server 0.0.0.0:8080
pipeline server -> VLM server 127.0.0.1:8081/v1
```

This is an experiment only. Do not connect it to the DocuParse production
backend until output structure, timing, and guardrails are validated.

## Important Compatibility Note

The official images split pipeline and VLM roles for a reason. A previous
experiment showed that installing Paddle/PaddleX pipeline dependencies directly
inside the VLM recognition image can break Torch/vLLM CUDA imports with an error
similar to:

```text
__nvJitLinkCreate_12_8 symbol mismatch
```

This PoC therefore uses the `paddleocr-vl:latest-nvidia-gpu` image as the base
and adds only the lightweight PaddleOCR/PaddleX packages needed for
`paddleocr genai_server`. If that still causes CUDA/Torch/Paddle/vLLM conflicts,
fall back to the official two-container Docker Compose deployment instead.

## Build

Build and push from a machine or CI runner with Docker and registry access:

```bash
docker build \
  -t ghcr.io/<owner>/docuparse-paddleocr-vl-singlepod:latest \
  infra/paddleocr-vl-singlepod

docker push ghcr.io/<owner>/docuparse-paddleocr-vl-singlepod:latest
```

Do not bake models, sample files, uploads, logs, database files, or GGUF files
into the image.

## RunPod Template

Suggested custom template values:

- Template name: `paddleocr-vl-singlepod-test`
- Container image: `ghcr.io/<owner>/docuparse-paddleocr-vl-singlepod:latest`
- Container disk: `80GB` minimum, `100GB` preferred
- Persistent storage: `80GB` minimum
- Persistent storage mount path: `/workspace`
- HTTP service port: `8080`
- Start command: use image CMD, or `/start-singlepod.sh`
- Environment:
  - `HF_HOME=/workspace/.cache/huggingface`
  - `PADDLE_PDX_MODEL_SOURCE=HUGGINGFACE`
  - `VLM_PORT=8081`
  - `VLM_MODEL_NAME=PaddleOCR-VL-1.6-0.9B`

Start with L4 x1. If model loading or parser inference hits VRAM limits, retry
on L40/L40S/RTX 4090 before considering production changes.

## Health Checks

Inside the Pod:

```bash
curl -s http://127.0.0.1:8081/v1/models
curl -s http://127.0.0.1:8080/health || true
curl -s http://127.0.0.1:8080/openapi.json || true
nvidia-smi
ps aux | grep -E "paddleocr|paddlex|vllm|genai|python" | grep -v grep
```

The pipeline endpoint, not the VLM endpoint, is the one to test for official
page-level output.

## Smoke Plan

Use fixture data only. Start with one page:

- `MFG-005_incoming_inspection_uncropped.png`

Verify that the response contains page-level parser artifacts:

- `parsing_res_list`
- table block
- HTML or markdown table
- row/column structure

Only after that, test:

- `MFG-003_delivery_no_price_uncropped.jpg`
- `MFG-005_incoming_inspection_uncropped.png`
- `MFG-007_return_credit_uncropped.pdf`
- `MFG-010...` representative delivery sample
- `MFG-014_commercial_invoice_hidden_amount.pdf`

Record per-document wall time, VRAM, timeout/OOM status, table block existence,
and guardrail risks. The result is a benchmark, not a production switch.

## Decision Rules

- `2-4s/page`: promising, continue adapter and guardrail validation.
- `6-9s/page`: similar to the current GGUF path; switching may not be worth it.
- `10s+ or unstable`: not a production candidate.
- Fast but hallucinated hidden/no-price amounts: do not use as confirmed
  extraction.
