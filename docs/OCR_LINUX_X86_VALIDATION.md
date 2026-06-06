# Linux x86_64 OCR Worker Validation

This guide verifies whether the isolated `ocr-worker` Docker service can run
PaddleOCR inference on a deployment-grade Linux x86_64 host.

The MacBook M2/arm64 Docker path can import PaddleOCR but currently crashes
during native inference with `SIGSEGV`. The production validation target is a
Linux x86_64/amd64 Docker host.

## Run The Full Check

From the repository root on the Linux x86_64 server:

```bash
scripts/verify_linux_x86_ocr_worker.sh
```

The script performs:

- host architecture check with `uname -m`
- Docker server architecture check
- `docker compose down -v`
- `docker compose up -d --build db ocr-worker backend frontend`
- backend `/health`
- ocr-worker `/health`
- OCR worker smoke test with a synthetic image
- image-only PDF upload through the backend API
- latest document OCR metadata summary
- provider assertion for `ocr_provider_succeeded=ocr_worker_paddleocr`
- backend and ocr-worker log tail on failure

## Optional Environment Variables

```bash
SAMPLE_PDF=samples/pdf_samples/docuparse_image_based_pdf_samples_10/03_image_invoice_vendor_sku.pdf \
WAIT_TIMEOUT_SECONDS=420 \
scripts/verify_linux_x86_ocr_worker.sh
```

Useful variables:

- `SAMPLE_PDF`: image-only PDF sample to upload.
- `WAIT_TIMEOUT_SECONDS`: how long to wait for backend processing.
- `BACKEND_HEALTH_URL`: defaults to `http://localhost:8001/health`.
- `OCR_WORKER_HEALTH_URL`: defaults to `http://localhost:8010/health`.
- `API_BASE_URL`: defaults to `http://localhost:8001/api`.

## Expected Success

The final metadata should include:

```json
{
  "ocr_provider_attempted": ["ocr_worker_paddleocr"],
  "ocr_provider_succeeded": "ocr_worker_paddleocr",
  "ocr_fallback_used": false
}
```

The exact `document_type` and `line_items_count` depend on the sample, but the
provider success assertion must print:

```text
linux_x86_ocr_worker_validation=PASS
```

## Expected Fallback

If PaddleOCR fails but DocuParse remains stable, metadata should show a fallback:

```json
{
  "ocr_provider_attempted": ["ocr_worker_paddleocr", "tesseract"],
  "ocr_provider_succeeded": "tesseract",
  "ocr_provider_failed_reason": {
    "ocr_worker_paddleocr": "..."
  },
  "ocr_fallback_used": true
}
```

The script classifies logs when possible:

- `PADDLE_RUNTIME_INFERENCE_ERROR`
- `SIGSEGV_NATIVE_RUNTIME_CRASH`
- `TIMEOUT`
- `DEPENDENCY_ERROR`
- `MODEL_DOWNLOAD_OR_CACHE_EVENT`
- `UNKNOWN_CHECK_LOGS`

The worker is pinned to a conservative Linux CPU OCR runtime:

- `numpy==1.26.4`
- `paddleocr==2.7.3`
- `paddlepaddle==2.6.2`
- PaddleOCR legacy `.ocr(...)` API
- `PADDLEOCR_LANG=korean`
- `PADDLEOCR_OCR_VERSION=PP-OCRv4`

This avoids the PaddleOCR 3.x PaddleX/PIR path that can silently instantiate
PP-OCRv5 server models and fail on some CPU-only Linux hosts with errors such
as `ConvertPirAttribute2RuntimeAttribute ... onednn_instruction`.

The worker also sets conservative runtime flags:

- `FLAGS_use_onednn=0`
- `FLAGS_use_mkldnn=0`
- `FLAGS_enable_pir_api=0`
- `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`
- `PADDLE_DISABLE_SIGNAL_HANDLER=1`

The flags remain as guardrails, but the dependency pin is the primary fix.
`opencv-python-headless==4.10.0.84` is installed with the same global
`numpy==1.26.4` constraint in both the base backend requirements and the OCR
requirements. The Docker build runs an import check after dependency install so
a NumPy 2.x ABI mismatch fails during image build instead of at worker startup.

## PaddleOCR Worker Stability

PaddleOCR 2.x uses a native predictor that can be unstable when a worker process
reuses the predictor across concurrent real PDF page requests. The worker
therefore serializes PaddleOCR inference with a process-level lock. If Paddle
raises a recoverable runtime error such as `Tensor holds no memory`,
`PreconditionNotMet`, `holder_ should not be null`, `elementwise_mul`, or
`elementwise_add`, the worker resets the provider and retries the same OCR
request once.

Successful retry responses include:

```json
{
  "ok": true,
  "engine_name": "ocr_worker_paddleocr",
  "retry_used": true,
  "provider_reset_used": true,
  "worker_attempt_count": 2
}
```

If the retry also fails, the worker returns a structured `500` response with
`retry_used=true`, `provider_reset_used=true`, and `elapsed_ms`. The backend then
keeps the existing Tesseract fallback path and records the worker failure reason
in document OCR metadata.

## Log Locations

Use these commands after a failed validation:

```bash
docker compose logs --tail=200 ocr-worker
docker compose logs --tail=200 backend
docker compose ps
curl http://localhost:8001/health
curl http://localhost:8010/health
```

## Manual Metadata Check

Inside the backend container:

```bash
docker compose exec -T backend \
  env DOCUPARSE_API_BASE=http://localhost:8000/api \
  python -m app.scripts.print_latest_ocr_metadata --wait
```

Or for a specific document:

```bash
docker compose exec -T backend \
  env DOCUPARSE_API_BASE=http://localhost:8000/api DOCUPARSE_DOCUMENT_ID=<document-id> \
  python -m app.scripts.print_latest_ocr_metadata --wait
```

## Notes

- Do not treat `paddleocr_importable=true` as proof of inference success.
  Inference success is proven only when `ocr_provider_succeeded` is
  `ocr_worker_paddleocr`.
- Tesseract fallback is expected to remain available even if the worker crashes.
- The `paddleocr_cache` Docker volume keeps model files between runs unless the
  validation script runs `docker compose down -v`.
