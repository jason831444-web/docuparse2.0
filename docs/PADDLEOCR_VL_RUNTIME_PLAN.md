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
- Backend heavy provider setting: `AI_PRIMARY_PROVIDER=paddleocr_vl_onnx_quantized`
- Actual PaddleOCR-VL ONNX status today: disabled/degraded unless
  `ENABLE_PADDLEOCR_VL_ONNX=true`, `onnxruntime` is installed, and an ONNX
  quantized model bundle is mounted.

The PP-OCRv4 worker must remain available even when PaddleOCR-VL is enabled.

## Target Provider Chain

1. `paddleocr_vl_onnx_quantized`
   - Model: `PaddleOCR-VL-1.5-ONNX-quantized`
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

The PaddleOCR-VL ONNX stack is intentionally optional. The minimal runtime
should stay separate from the PP-OCRv4 worker:

```text
onnxruntime>=1.20.0
numpy>=1.26.0
pillow>=10.0.0
opencv-python-headless>=4.9.0
```

If the ONNX bundle still requires a tokenizer/processor from the original
PaddleOCR-VL repository, add those dependencies in a dedicated VL container.
Do not install PaddleOCR 3.x into the legacy `ocr-worker`.

## PaddleOCR 2.x / 3.x Collision Risk

The existing PP-OCRv4 worker is pinned to:

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
```

The ONNX quantized path should avoid installing PaddleOCR 3.x into the same
process as the legacy worker. Mixing both stacks can change import behavior and
route legacy OCR through PaddleX/PIR paths. The PP-OCRv4 worker must stay
isolated. A future `vl-worker` container is the safest home for the ONNX
document parser once the model bundle and processor contract are finalized.

## Recommended Container Shape

Short term:

- Backend may install ONNX dependencies with `INSTALL_AI_DEPS=true`.
- OCR worker keeps `INSTALL_AI_DEPS=false`.
- Backend tries `paddleocr_vl_onnx_quantized` for heavy image documents only
  when `ENABLE_PADDLEOCR_VL_ONNX=true`.
- OCR worker remains PP-OCRv4 fallback.

Long term:

- Add a separate `vl-worker` service for PaddleOCR-VL ONNX.
- Keep `ocr-worker` small and stable for PP-OCRv4.
- Route through backend provider chain:
  `vl-worker -> ocr-worker -> tesseract`.

## Environment

```bash
ENABLE_PADDLEOCR_VL_ONNX=true
AI_PRIMARY_PROVIDER=paddleocr_vl_onnx_quantized
PADDLEOCR_VL_ONNX_MODEL_NAME=PaddleOCR-VL-1.5-ONNX-quantized
PADDLEOCR_VL_ONNX_MODEL_PATH=/app/models/paddleocr_vl_onnx_quantized
PADDLEOCR_VL_ONNX_REPO_ID=lbm364dl/PaddleOCR-VL-1.5-ONNX
PADDLEOCR_VL_ONNX_DEVICE=cpu
PADDLEOCR_VL_ONNX_TIMEOUT_SECONDS=60
PADDLEOCR_VL_ONNX_RUNTIME_VERSION=1.23.2
PADDLEOCR_VL_ONNX_RUNNER_MODULE=
OCR_WORKER_URL=http://ocr-worker:8010
PREFER_OCR_WORKER=true
```

## Model Cache

- Docker model volume: `/app/models`
- ONNX model bundle path: `/app/models/paddleocr_vl_onnx_quantized`
- Legacy PaddleOCR/PaddleX cache: `/root/.paddlex`

When using Docker named volumes, verify that
`/app/models/paddleocr_vl_onnx_quantized` contains `.onnx` files plus the
required tokenizer/processor files. A named volume can shadow files copied into
the image.

DocuParse includes a minimal experimental runner at
`app.services.paddleocr_vl_onnx_runner`. A custom executable runner module can
be named by `PADDLEOCR_VL_ONNX_RUNNER_MODULE`. The module must expose:

```python
def predict(*, image_path: str, model_path: str, model_files: list[str], device: str, timeout_seconds: float, max_pages: int) -> dict:
    ...
```

The returned dict should contain optional `text`, `line_candidates`,
`table_candidates`, `layout_elements`, and `raw_blocks` fields. Without this
runner path, health intentionally reports a runner/import error and continues
with PP-OCRv4 fallback. Even with a runner, health remains unavailable until a
real sample inference produces validated text and a validation marker is
written under the model directory.

## CPU Cost Expectations

PaddleOCR-VL is a compact VLM, but it is still far heavier than PP-OCRv4.
Expected CPU behavior:

- cold start can be slow because model weights must load;
- per-page inference can be substantially slower than PP-OCRv4;
- memory pressure is likely on small Docker Desktop allocations;
- timeout fallback must remain enabled;
- production usage should prefer GPU or a dedicated worker if throughput matters.

## Activation Criteria

Do not treat `AI_PRIMARY_PROVIDER=paddleocr_vl_onnx_quantized` as proof that PaddleOCR-VL is
active. Activation requires:

- `/health.providers.primary_provider_available=true`
- `/health.providers.ocr_engine=PaddleOCR-VL ONNX`
- `/health.providers.paddleocr_vl_onnx_probe.runner_module` is importable
- `/health.providers.paddleocr_vl_onnx_probe.validation_marker` exists and
  records `output_validation_status=candidate_text_generated`
- provider metadata contains `document_ai_succeeded=true`
- provider chain contains `paddleocr_vl_onnx_quantized` without
  `paddleocr_vl_onnx_quantized_unavailable`
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
