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

The worker is configured for conservative Linux CPU inference by default:

- `FLAGS_use_onednn=0`
- `FLAGS_use_mkldnn=0`
- `FLAGS_enable_pir_api=0`
- `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`
- `PADDLE_DISABLE_SIGNAL_HANDLER=1`
- `PADDLEOCR_OCR_VERSION=PP-OCRv4`
- `PADDLEOCR_DET_MODEL=PP-OCRv4_mobile_det`
- `PADDLEOCR_REC_MODEL=korean_PP-OCRv4_mobile_rec`

These defaults avoid the PP-OCRv5 server + oneDNN/PIR path that can fail on
some CPU-only Docker hosts with errors such as
`ConvertPirAttribute2RuntimeAttribute not support ... onednn_instruction`.

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
