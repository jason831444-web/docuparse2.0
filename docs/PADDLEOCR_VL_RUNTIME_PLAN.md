# PaddleOCR-VL Runtime Plan

DocuParse currently keeps the stable PP-OCRv4 worker as the fallback OCR path
and treats PaddleOCR-VL as an optional primary document parsing provider. This
document records the runtime work needed before PaddleOCR-VL can be enabled as
the default provider in production.

## Current Production Path

- `ocr-worker`: PaddleOCR 2.x legacy API
- OCR model: `PP-OCRv4`
- Language: `korean`
- Device: CPU
- Runtime strategy: `paddleocr_2x_legacy_ocr_api`
- Backend heavy provider setting: `AI_PRIMARY_PROVIDER=paddleocr_vl`
- Actual PaddleOCR-VL status today: degraded unless AI dependencies are
  installed and `PaddleOCRVL` can be imported.

The PP-OCRv4 worker must remain available even when PaddleOCR-VL is enabled.

## Target Provider Chain

1. `paddleocr_vl`
   - Model: `PaddleOCR-VL-1.6`
   - Use for image/scanned/photographed documents that route to heavy document
     understanding.
   - Normalize output into text/table candidates before existing parser,
     taxonomy, review, and export guardrails.
2. `paddleocr_ppocrv4`
   - Existing isolated OCR worker fallback.
   - Must handle import/load/timeout/empty-output failures from PaddleOCR-VL.
3. Tesseract fallback
   - Existing last-resort OCR fallback if the worker fails.

## Required Dependencies

The PaddleOCR-VL stack is intentionally optional because it upgrades the OCR
runtime family:

```text
paddleocr==3.6.0
paddlex[ocr]==3.6.0
torch>=2.5.0
transformers>=4.58.0
accelerate>=1.2.0
sentencepiece>=0.2.0
protobuf>=5.29.0
huggingface_hub>=0.27.0
hf_xet>=1.1.0
```

`qwen-vl-utils` is only needed for the Qwen fallback provider.

## PaddleOCR 2.x / 3.x Collision Risk

The existing PP-OCRv4 worker is pinned to:

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
```

PaddleOCR-VL needs the newer PaddleOCR/PaddleX runtime. Installing it in the
same process as the legacy worker can change import behavior and route legacy
OCR through PaddleX/PIR paths. That is why the PP-OCRv4 worker must stay
isolated and why PaddleOCR-VL should run in the backend heavy provider or in a
separate future `vl-worker` container.

## Recommended Container Shape

Short term:

- Backend may install AI dependencies with `INSTALL_AI_DEPS=true`.
- OCR worker keeps `INSTALL_AI_DEPS=false`.
- Backend tries PaddleOCR-VL for heavy image documents.
- OCR worker remains PP-OCRv4 fallback.

Long term:

- Add a separate `vl-worker` service for PaddleOCR-VL.
- Keep `ocr-worker` small and stable for PP-OCRv4.
- Route through backend provider chain:
  `vl-worker -> ocr-worker -> tesseract`.

## Environment

```bash
ENABLE_PADDLEOCR_VL=true
AI_PRIMARY_PROVIDER=paddleocr_vl
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6
PADDLEOCR_VL_HF_REPO=PaddlePaddle/PaddleOCR-VL-1.6
PADDLEOCR_VL_MODEL_DIR=/app/models/paddleocr_vl
PADDLEOCR_VL_DEVICE=cpu
PADDLEOCR_VL_TIMEOUT_SECONDS=180
OCR_WORKER_URL=http://ocr-worker:8010
PREFER_OCR_WORKER=true
```

## Model Cache

- Docker model volume: `/app/models`
- PaddleOCR/PaddleX cache: `/root/.paddlex`
- Existing local model path: `/app/models/paddleocr_vl`

When using Docker named volumes, verify that `/app/models/paddleocr_vl` contains
the intended PaddleOCR-VL-1.6 files. A named volume can shadow files copied into
the image.

## CPU Cost Expectations

PaddleOCR-VL is a compact VLM, but it is still far heavier than PP-OCRv4.
Expected CPU behavior:

- cold start can be slow because model weights must load;
- per-page inference can be substantially slower than PP-OCRv4;
- memory pressure is likely on small Docker Desktop allocations;
- timeout fallback must remain enabled;
- production usage should prefer GPU or a dedicated worker if throughput matters.

## Activation Criteria

Do not treat `AI_PRIMARY_PROVIDER=paddleocr_vl` as proof that PaddleOCR-VL is
active. Activation requires:

- `/health.providers.primary_provider_available=true`
- `/health.providers.ocr_engine=PaddleOCR-VL`
- provider metadata contains `document_ai_succeeded=true`
- provider chain contains `paddleocr_vl` without `paddleocr_vl_unavailable`
- row-level E2E shows no fake quantity/amount/currency regressions

If any of these fail, DocuParse must report degraded mode and continue through
PP-OCRv4 fallback.

## Benchmark Before Production Default

Before enabling PaddleOCR-VL by default, run the 34 PDF regression and compare:

- provider used / fallback used
- OCR text length
- line candidate count
- table candidate count
- confirmed line item count
- review candidate count
- hallucinated quantity/amount count
- no-price fake total/currency count
- document number/type/total match
- row-level expected match
- processing time and timeout count

PaddleOCR-VL output must pass the existing parser, taxonomy, review, and export
guardrails. VLM table output must not be promoted directly to confirmed
`line_items` without validation.
