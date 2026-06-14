# PaddleOCR-VL GGUF Deployment

This guide prepares the official PaddleOCR-VL-1.6 GGUF candidate path without
committing model files or enabling it as production primary by default.

## Rules

- Use only `PaddlePaddle/PaddleOCR-VL-1.6-GGUF`.
- Do not commit `.gguf`, `.onnx`, `.bin`, `.pt`, `.pth`, or `.safetensors`
  files.
- Do not expose `llama-server` publicly.
- Keep PP-OCRv4 fallback enabled.
- Do not run PaddleOCR-VL inference on the Mac development machine.

## Download Model Files

On the Linux server:

```bash
cd /root/docuparse2.0
PYTHONPATH=backend python3 -m app.scripts.download_paddleocr_vl_gguf \
  --repo-id PaddlePaddle/PaddleOCR-VL-1.6-GGUF \
  --target /root/docuparse_models/paddleocr_vl_1_6_gguf \
  --output-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_download
```

Expected files:

```text
PaddleOCR-VL-1.6-GGUF.gguf
PaddleOCR-VL-1.6-GGUF-mmproj.gguf
README.md
chat_template.jinja
SHA256SUMS.txt
```

## Start GGUF Worker

The compose service is internal-only and behind the `vl` profile:

```bash
export PADDLEOCR_VL_GGUF_HOST_MODEL_DIR=/root/docuparse_models/paddleocr_vl_1_6_gguf
docker compose --profile backend --profile vl up -d vl-worker-gguf
```

Check the local server:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/v1/models
```

The backend reaches it through:

```text
http://vl-worker-gguf:8080/v1
```

## Smoke 08 First

Run only the first smoke sample:

```bash
cd /root/docuparse2.0
PYTHONPATH=backend PADDLEOCR_VL_GGUF_MODEL_DIR=/root/docuparse_models/paddleocr_vl_1_6_gguf \
PADDLEOCR_VL_GGUF_SERVER_URL=http://127.0.0.1:8080/v1 \
timeout 600s python3 -m app.scripts.smoke_paddleocr_vl_gguf \
  --sample samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf \
  --output-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke
```

Expected validation terms include:

- `QT-2026-0808-009`
- `고정`
- `플레이트`
- `스테인리스`
- `473,000`

The first item quantity is blank in the source PDF and should stay blank/null.

## Enabling Candidate Health

Do not set `PADDLEOCR_VL_GGUF_SMOKE_PASSED=true` until the staged smoke path has
passed through the service deployment. A running `llama-server` alone is not
enough.

Suggested gated rollout:

1. `08_image_quote_missing_quantity.pdf`
2. `16_real_commercial_invoice_exchange_rate.pdf`
3. `21_photo_fax_po_misaligned_amounts.pdf`
4. 34-PDF regression comparison

Only then should the candidate be considered for broader provider routing.

## Troubleshooting

- `model_missing`: check `PADDLEOCR_VL_GGUF_HOST_MODEL_DIR` and expected file
  names.
- `llama_server_unreachable`: check the `vl-worker-gguf` container and compose
  network.
- `document_terms_missing`: the model returned readable output, but not enough
  document evidence.
- `degenerate_generation`: keep PP-OCRv4 fallback; do not promote provider.
- high memory use: keep concurrency at 1 and max pages at 1.
