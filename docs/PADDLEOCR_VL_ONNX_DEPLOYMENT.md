# PaddleOCR-VL ONNX Deployment

DocuParse keeps PP-OCRv4 as the stable OCR fallback. PaddleOCR-VL ONNX is an
opt-in primary candidate provider. Model binaries are never committed to git.

## Model

- Repo: `lbm364dl/PaddleOCR-VL-1.5-ONNX`
- License: Apache-2.0
- Runtime target: `onnxruntime==1.23.2`
- Container path: `/app/models/paddleocr_vl_onnx_quantized`
- Fallback provider: `paddleocr_ppocrv4`

Required bundle files:

- `onnx/decoder_model_merged.onnx`
- `onnx/embed_tokens.onnx`
- `onnx/vision_encoder.onnx`
- `config.json`
- `tokenizer.json`
- `tokenizer.model`
- `processor_config.json`
- `preprocessor_config.json`

## Git Safety

The repository ignores local model paths and large model files:

- `models/`
- `backend/models/`
- `*.onnx`
- `*.onnx_data`
- `*.safetensors`
- `*.bin`
- `*.gguf`
- `*.pt`
- `*.pth`

Do not copy model files into tracked source directories.

## Download

Download on the server or into a Docker volume. The downloader skips existing
complete bundles unless `--force` is passed.

Host path example:

```bash
cd /root/docuparse2.0
python3 backend/app/scripts/download_paddleocr_vl_onnx.py \
  --repo-id lbm364dl/PaddleOCR-VL-1.5-ONNX \
  --target /root/docuparse_models/paddleocr_vl_onnx_quantized
```

Named Docker volume example:

```bash
docker compose up -d backend
docker compose exec backend python -m app.scripts.download_paddleocr_vl_onnx \
  --repo-id lbm364dl/PaddleOCR-VL-1.5-ONNX \
  --target /app/models/paddleocr_vl_onnx_quantized
```

If you prefer a bind mount, mount the host model directory to `/app/models` and
keep `PADDLEOCR_VL_ONNX_MODEL_PATH=/app/models/paddleocr_vl_onnx_quantized`.

## Environment

Keep the provider disabled until the model bundle and runner smoke pass.

```bash
AI_PRIMARY_PROVIDER=paddleocr_vl_onnx_quantized
ENABLE_PADDLEOCR_VL_ONNX=false
PADDLEOCR_VL_ONNX_MODEL_PATH=/app/models/paddleocr_vl_onnx_quantized
PADDLEOCR_VL_ONNX_MODEL_NAME=PaddleOCR-VL-1.5-ONNX-quantized
PADDLEOCR_VL_ONNX_REPO_ID=lbm364dl/PaddleOCR-VL-1.5-ONNX
PADDLEOCR_VL_ONNX_DEVICE=cpu
PADDLEOCR_VL_ONNX_TIMEOUT_SECONDS=60
PADDLEOCR_VL_ONNX_MAX_PAGES=1
PADDLEOCR_VL_ONNX_RUNTIME_VERSION=1.23.2
OCR_FALLBACK_PROVIDER=paddleocr_ppocrv4
```

Enable only for smoke:

```bash
export ENABLE_PADDLEOCR_VL_ONNX=true
```

## Smoke

Session load probe:

```bash
docker compose exec backend python -m app.scripts.smoke_paddleocr_vl_onnx \
  --model-path /app/models/paddleocr_vl_onnx_quantized \
  --check-sessions \
  --onnxruntime-version-report \
  --output-dir /tmp/docuparse_e2e_logs/vl_onnx_session_smoke
```

First text-output smoke:

```bash
docker compose exec backend python -m app.scripts.smoke_paddleocr_vl_onnx \
  --model-path /app/models/paddleocr_vl_onnx_quantized \
  --run-inference \
  --sample samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf \
  --max-new-tokens 64 \
  --output-dir /tmp/docuparse_e2e_logs/vl_onnx_runner_smoke
```

The provider is active only when generated output passes validation. Empty
output, prompt echo, repeated garbage text, broken unicode, and timeout all
remain fallback conditions.

Do not use `--write-validation-marker` unless the sample output is grounded and
valid. The health endpoint reads `.docuparse_vl_onnx_validated.json` under the
model path before it reports PaddleOCR-VL ONNX as available.

## Health

Fallback is expected before a validated smoke marker exists:

```json
{
  "primary_provider": "paddleocr_vl_onnx_quantized",
  "primary_provider_available": false,
  "fallback_provider": "paddleocr_ppocrv4",
  "fallback_reason": "paddleocr_vl_onnx_inference_not_validated",
  "runtime_strategy": "ppocrv4_fallback"
}
```

PaddleOCR-VL ONNX should only become available after:

1. The bundle is complete.
2. `onnxruntime` and tokenizer dependencies are installed.
3. ONNX sessions load.
4. A sample inference produces valid document-grounded text.
5. The smoke command writes a validation marker.

## Troubleshooting

- `paddleocr_vl_onnx_disabled`: opt-in flag is off.
- `paddleocr_vl_onnx_model_path_missing`: model volume/path is not mounted.
- `paddleocr_vl_onnx_model_missing`: one or more ONNX files are missing.
- `paddleocr_vl_onnx_processor_missing`: tokenizer/config files are missing.
- `onnxruntime_missing`: build backend with `INSTALL_AI_DEPS=true` or install
  the optional AI requirements.
- `paddleocr_vl_onnx_inference_not_validated`: session files exist, but no
  successful text-output smoke marker exists.
- `degenerate_generation`: the ONNX runner generated repeated/broken text and
  must fall back to PP-OCRv4.

## Reset

To remove and re-download:

```bash
rm -rf /root/docuparse_models/paddleocr_vl_onnx_quantized
python3 backend/app/scripts/download_paddleocr_vl_onnx.py \
  --repo-id lbm364dl/PaddleOCR-VL-1.5-ONNX \
  --target /root/docuparse_models/paddleocr_vl_onnx_quantized
```

## Current Limitation

The Linux ONNX graph can load on `onnxruntime==1.20.1` and `1.23.2`, but the
minimal Python runner still produces degenerate generation on the 08_image smoke
sample. Until the runner contract is fixed or a transformers.js/vl-worker path
is proven, DocuParse must keep `provider_available=false` and use PP-OCRv4
fallback in production.
