# PaddleOCR-VL Runtime Plan

DocuParse keeps PP-OCRv4 as the stable production OCR fallback and treats
PaddleOCR-VL as a gated candidate path. The current candidate is the official
PaddleOCR-VL-1.6 GGUF runtime served by `llama-server`; it is not enabled as a
production primary provider by default.

## Provider Chain

1. `paddleocr_vl_1_6_gguf`
   - Source: `PaddlePaddle/PaddleOCR-VL-1.6-GGUF`
   - Runtime: `llama.cpp` `llama-server`
   - Backend from PaddleOCR: `vl_rec_backend="llama-cpp-server"`
   - Role: candidate document text/table/layout evidence.
   - Default state: disabled.
2. `paddleocr_ppocrv4`
   - Existing OCR worker fallback.
   - Must remain available for upload, parsing, review, and export flows.
3. Tesseract fallback
   - Last-resort fallback if worker OCR is unavailable.

VL output is never final truth. It must stay candidate evidence until the
existing parser, taxonomy, review, and export guardrails validate it.

## Full PaddleOCR-VL-1.6 Result

Official full PaddleOCR-VL-1.6 successfully produced readable text on
`08_image_quote_missing_quantity.pdf` on the Linux server, but it is not a
practical primary provider on the current 8GB / 2-core CPU server:

- one page took about 4 minutes;
- peak RSS was about 5.5GiB;
- swap usage was about 3.7GiB-5.8GiB.

Its status is therefore:

```text
paddleocr_vl_official_full = memory_blocked_on_8gb_cpu
```

## GGUF Server Smoke Result

The official GGUF path succeeded on the same server:

- repo: `PaddlePaddle/PaddleOCR-VL-1.6-GGUF`
- model path: `/root/docuparse_models/paddleocr_vl_1_6_gguf`
- files:
  - `PaddleOCR-VL-1.6-GGUF.gguf`
  - `PaddleOCR-VL-1.6-GGUF-mmproj.gguf`
- `llama.cpp` commit used for smoke: `c2ba3e47a`
- sample: `08_image_quote_missing_quantity.pdf`
- elapsed: about 95 seconds;
- observed llama-server RSS: about 2.0GiB;
- observed Python PaddleOCRVL RSS: about 0.8-0.9GiB;
- swap: about 458-466MiB.

The output contained real document terms including `QT-2026-0808-009`,
`고정 플레이트`, `스테인리스`, and `473,000`, and it preserved the first
row quantity as blank.

## Environment

```bash
AI_PRIMARY_PROVIDER=paddleocr_vl_1_6_gguf
AI_SECONDARY_PROVIDER=heuristic_fallback
OCR_FALLBACK_PROVIDER=paddleocr_ppocrv4

ENABLE_PADDLEOCR_VL_GGUF=false
PADDLEOCR_VL_GGUF_REPO_ID=PaddlePaddle/PaddleOCR-VL-1.6-GGUF
PADDLEOCR_VL_GGUF_MODEL_DIR=/app/models/paddleocr_vl_1_6_gguf
PADDLEOCR_VL_GGUF_MODEL_FILE=PaddleOCR-VL-1.6-GGUF.gguf
PADDLEOCR_VL_GGUF_MMPROJ_FILE=PaddleOCR-VL-1.6-GGUF-mmproj.gguf
PADDLEOCR_VL_GGUF_SERVER_URL=http://vl-worker-gguf:8080/v1
PADDLEOCR_VL_GGUF_TIMEOUT_SECONDS=120
PADDLEOCR_VL_GGUF_MAX_PAGES=1
PADDLEOCR_VL_GGUF_CONCURRENCY=1
PADDLEOCR_VL_GGUF_SMOKE_PASSED=false

OCR_WORKER_URL=http://ocr-worker:8010
PREFER_OCR_WORKER=true
```

`PADDLEOCR_VL_GGUF_SMOKE_PASSED` must remain false until the service path has
passed the staged smoke set. A running `llama-server` alone is not enough to
mark the provider available.

## Health Semantics

Health distinguishes:

- `disabled`
- `model_missing`
- `llama_server_unreachable`
- `llama_server_ready`
- `smoke_not_run`
- `active_candidate`
- `fallback`

`primary_provider_available=true` requires all of the following:

- GGUF provider enabled;
- GGUF model and mmproj files present;
- llama-server health OK;
- service smoke gate marked as passed;
- PP-OCRv4 fallback remains available.

## Guardrails

- Do not run PaddleOCR-VL inference on the Mac development machine.
- Do not commit model files.
- Do not run 34-PDF E2E with VL until `08`, `16_real`, and `21_photo` smoke
  are approved.
- Do not insert VL output into confirmed `line_items`.
- Do not fabricate quantities, amounts, totals, currency, or item names.
- Keep bbox/review candidates separate from confirmed extraction.
- Keep no-price document fake-total/currency prevention intact.

## Staged Rollout

1. `08_image_quote_missing_quantity.pdf`
   - Verify readable text.
   - Verify blank quantity remains blank.
2. `16_real_commercial_invoice_exchange_rate.pdf`
   - Verify 3 rows.
   - Verify exchange rate is not treated as total.
3. `21_photo_fax_po_misaligned_amounts.pdf`
   - Verify row evidence improves or remains review candidate only.
4. Only after those pass, compare against the 34-PDF regression set.
