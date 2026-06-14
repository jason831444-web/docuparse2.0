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

Check the compose worker from Docker, because it is intentionally not published
on the host network:

```bash
docker compose ps vl-worker-gguf
docker inspect --format='{{json .State.Health}}' docuparse20-vl-worker-gguf-1
```

The backend reaches it through:

```text
http://vl-worker-gguf:8080/v1
```

## Server-Isolated Smoke 08 First

The GGUF inference smoke must be run on Linux only. The helper below refuses to
run on macOS, starts a host-local `llama-server` on `127.0.0.1:8081`, optionally
stops the compose candidate worker to free memory, and restores it with the same
model mount after the smoke.

```bash
cd /root/docuparse2.0
PADDLEOCR_VL_GGUF_MODEL_DIR=/root/docuparse_models/paddleocr_vl_1_6_gguf \
scripts/run_paddleocr_vl_gguf_server_smoke.sh
```

Expected validation terms include:

- `QT-2026-0808-009`
- `고정`
- `플레이트`
- `스테인리스`
- `473,000`

The first item quantity is blank in the source PDF and should stay blank/null.

Manual visual verification should be provided with `MANUAL_VISUAL_CHECK_FILE`
when promoting a smoke result beyond quick diagnostics:

```bash
MANUAL_VISUAL_CHECK_FILE=/tmp/08_manual_visual_check.json \
PADDLEOCR_VL_GGUF_MODEL_DIR=/root/docuparse_models/paddleocr_vl_1_6_gguf \
scripts/run_paddleocr_vl_gguf_server_smoke.sh
```

For targeted follow-up samples, override `SAMPLE`, `OUTPUT_DIR`, and optionally
`PADDLEOCR_VL_GGUF_RENDER_SCALE`:

```bash
SAMPLE=samples/pdf_samples/docuparse_realistic_photographed_pdf_samples/pdfs/21_photo_fax_po_misaligned_amounts.pdf \
PADDLEOCR_VL_GGUF_RENDER_SCALE=3.0 \
PADDLEOCR_VL_GGUF_MODEL_DIR=/root/docuparse_models/paddleocr_vl_1_6_gguf \
scripts/run_paddleocr_vl_gguf_server_smoke.sh
```

The raw smoke module also accepts explicit runtime overrides. This is useful for
backend-container diagnostics and host venv checks because it avoids editing
`.env` just to point at a mounted model directory or a temporary
`llama-server`:

```bash
PYTHONPATH=backend python3 -m app.scripts.smoke_paddleocr_vl_gguf \
  --sample samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf \
  --model-dir /root/docuparse_models/paddleocr_vl_1_6_gguf \
  --server-url http://127.0.0.1:8081/v1 \
  --concurrency 1 \
  --output-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/manual_08
```

## Summarize Staged Smoke Reports

After running `08`, `16_real`, and `21_photo`, summarize the reports before
changing any provider health or routing setting:

```bash
cd /root/docuparse2.0
PYTHONPATH=backend python3 -m app.scripts.summarize_paddleocr_vl_gguf_smokes \
  --input-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/08_manual_expected_0e265ed \
  --input-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/16_manual_expected_0e265ed \
  --input-dir /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/21_manual_expected_0e265ed \
  --output-json /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/summary.json \
  --output-md /tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/summary.md
```

The summary should keep `production_active_recommended=false` whenever any
manual visual check is `warn` or `fail`. The current staged result is one PASS
and two WARN reports, so the GGUF path remains candidate-only and PP-OCRv4
continues as the production OCR path.

The summary also includes per-sample `recommended_handling`:

- `candidate_evidence_only`: readable candidate evidence, still not confirmed
  ERP truth.
- `use_parser_primary_vl_auxiliary`: keep the validated text-layer/parser path
  primary and show VL only as auxiliary evidence.
- `review_candidate_only`: show VL output as review context only; do not
  promote it to confirmed fields.
- `reject_vl_candidate`: discard the VL candidate because manual validation
  found a dangerous error.

The default backend image intentionally remains the safe PP-OCRv4 runtime. If a
backend-container smoke run reports `paddleocr_vl_runtime_missing_dependency`,
that means the GGUF candidate runner dependencies are absent from the production
backend image, not that upload/OCR fallback is broken. Run GGUF inference from
the isolated server venv until a dedicated VL runner image is introduced.

## Enabling Candidate Health

Do not set `PADDLEOCR_VL_GGUF_SMOKE_PASSED=true` until the staged smoke path has
passed through the service deployment. A running `llama-server` alone is not
enough.

Keep `PADDLEOCR_VL_GGUF_IN_PROCESS_ENABLED=false` for the production backend.
The current safe path is isolated smoke or a future dedicated VL worker. If the
smoke gate has passed but in-process confirmed extraction is still disabled,
health should show `primary_reader_available=true`,
`primary_provider_candidate_available=true`, and
`primary_provider_available=false`. In this mode GGUF is the primary
reader/candidate source, while PP-OCRv4 remains the validation fallback and
confirmed ERP fields still go through parser/review guardrails.

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
