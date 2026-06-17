# Manufacturing Regression V1

This fixture set is a small, blocking-friendly subset of the larger smoke sample pools.
It focuses on manufacturing document automation rather than generic OCR demos.

## Scope

- Keep only representative manufacturing documents.
- Do not include POS, restaurant, franchise, or generic sales settlement samples.
- Treat handwritten, blurry, photographed, and cropped documents as review-first inputs.
- Never promote hidden/cropped amount columns into confirmed business data.
- Never create amounts for no-price delivery, inspection, or transfer documents.

## Fixture Layout

- `files/`: selected PDF/image samples plus per-file `.expected.json` files.
- `manifest.csv`: concise inventory for reviewers and automation.
- `expected_metadata.jsonl`: runner-friendly coarse expected metadata.
- `visual.md`: visible-ground-truth and business policy summary.

Run locally only for parser/unit tests. PaddleOCR-VL/GGUF inference must run on the Linux server.

```bash
PYTHONPATH=backend pytest -q backend/tests/test_generated_vl_primary_regression.py
```

Server smoke example:

```bash
PYTHONPATH=. python -m app.scripts.run_generated_vl_primary_regression \
  --sample-dir /tmp/manufacturing_regression_v1/files \
  --output-dir /tmp/docuparse_e2e_logs/manufacturing_regression_v1 \
  --api-base http://localhost:8000/api \
  --timeout-seconds 900 \
  --progress
```
