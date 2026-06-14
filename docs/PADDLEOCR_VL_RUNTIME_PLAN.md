# PaddleOCR-VL Runtime Plan

DocuParse uses the official PaddleOCR-VL provider as the primary document
understanding path and keeps the stable PP-OCRv4 worker as the fallback OCR
path. Experimental duplicate model paths have been removed from the runtime
surface to avoid ambiguous provider status and duplicate model stacks.

## Active Provider Chain

1. `paddleocr_vl`
   - Model: `PaddleOCR-VL-1.6`
   - Source: `PaddlePaddle/PaddleOCR-VL-1.6`
   - Device: CPU by default, configurable with `PADDLEOCR_VL_DEVICE`
   - Role: primary document layout/text/table candidate extraction for heavy
     image, scanned, or photographed documents.
2. `paddleocr_ppocrv4`
   - Existing OCR worker fallback.
   - Must remain available when PaddleOCR-VL import, model load, inference,
     timeout, or output normalization fails.
3. Tesseract fallback
   - Last-resort local OCR fallback if the worker path is unavailable.

PaddleOCR-VL output is not treated as final truth. It is normalized into text
and table evidence and must still pass the existing parser, taxonomy, review,
and export guardrails.

## Environment

```bash
AI_PRIMARY_PROVIDER=paddleocr_vl
AI_SECONDARY_PROVIDER=heuristic_fallback
AI_ENABLE_SECOND_PASS=false
OCR_FALLBACK_PROVIDER=paddleocr_ppocrv4

ENABLE_PADDLEOCR_VL=true
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6
PADDLEOCR_VL_HF_REPO=PaddlePaddle/PaddleOCR-VL-1.6
PADDLEOCR_VL_MODEL_DIR=
PADDLEOCR_VL_LAYOUT_MODEL_DIR=
PADDLEOCR_VL_DEVICE=cpu
PADDLEOCR_VL_TIMEOUT_SECONDS=180

OCR_WORKER_URL=http://ocr-worker:8010
PREFER_OCR_WORKER=true
```

## Model Cache

- Docker model volume: `/app/models`
- Default official PaddleOCR-VL cache is managed by PaddleX/PaddleOCR from
  `PADDLEOCR_VL_MODEL_NAME`.
- `PADDLEOCR_VL_MODEL_DIR` is optional. Set it only when the mounted model
  directory exactly matches `PADDLEOCR_VL_MODEL_NAME`; otherwise PaddleOCR will
  reject the model directory with a model-name mismatch.
- Legacy OCR worker remains isolated and should not be upgraded to PaddleOCR
  3.x in-place.

Model weights should be downloaded or mounted on the host/server. Large model
files must not be committed to the repository.

## Dependencies

`backend/requirements-ai.txt` contains only the official PaddleOCR-VL stack and
download helpers:

```text
paddleocr==3.6.0
paddlex[ocr]==3.6.0
huggingface_hub>=0.27.0
hf_xet>=1.1.0
```

The legacy `ocr-worker` keeps its PP-OCRv4/PaddleOCR 2.x stack. Do not install
the PaddleOCR-VL 3.x stack into the OCR worker container unless the worker is
redesigned and regression-tested.

## Health Semantics

Active PaddleOCR-VL requires:

- `primary_provider=paddleocr_vl`
- `primary_provider_available=true`
- `ocr_engine=PaddleOCR-VL`
- `ocr_model=PaddleOCR-VL-1.6`
- provider chain contains `paddleocr_vl` without `paddleocr_vl_unavailable`

If any import, model load, inference, or normalization step fails, health should
show degraded/fallback mode and the document flow should continue through
`paddleocr_ppocrv4`.

## Guardrails

PaddleOCR-VL must not bypass existing safety rules:

- do not fabricate quantities, amounts, totals, currency, or item names;
- no-price documents must not gain fake total/currency values;
- bbox/review candidates must stay separate from confirmed line items;
- uncertain rows and amount conflicts should stay Needs Review;
- row-level regression checks must pass before production rollout.

## Smoke Before Full E2E

Before running the full 34-PDF suite with PaddleOCR-VL active, smoke one or two
representative files:

```bash
PYTHONPATH=backend python3 backend/app/scripts/smoke_paddleocr_vl.py \
  --sample samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf \
  --output-dir /tmp/docuparse_e2e_logs/paddleocr_vl_smoke
```

Then verify:

- readable document text is produced;
- blank quantity cells remain blank/null;
- no hallucinated values are introduced;
- fallback metadata is explicit if PaddleOCR-VL fails.
