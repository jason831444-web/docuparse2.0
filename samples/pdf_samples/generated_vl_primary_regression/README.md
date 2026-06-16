# Generated VL Primary Regression Samples

Synthetic manufacturing PDFs for VL-first parser and validation hardening. Model binaries are not stored here. These PDFs are small regression fixtures.

Run `python3 samples/pdf_samples/generated_vl_primary_regression/generate_samples.py` to regenerate.

## Regression intent

This folder is the formal cropped/hidden-column regression set. It is separate from `pdf_samples_clear_visible_regression`, which is the clean baseline where the rendered page visibly contains the table columns needed for confirmed 업무데이터.

### Clear-visible baseline

Use `samples/pdf_samples/pdf_samples_clear_visible_regression/` when the page is fully visible.

Expected behavior:

- Visible document numbers, dates, parties, item names, specs, quantities, unit prices, supply amounts, tax amounts, and line totals may be promoted when parser/validation agrees.
- Business policy still wins over visibility: option quotes keep final totals unconfirmed until an option is selected, and no-price delivery/inspection/internal-transfer documents must not create amounts.
- Header rows, Vendor SKU labels, summary totals, exchange-rate notes, and review candidates must not leak into confirmed line items or export.

### Cropped/hidden-column set

Use this folder when the rendered visual fixture intentionally hides or truncates one or more right-side columns such as tax, amount, total, decision, remaining quantity, or remarks.

Expected behavior:

- A value that is not visible in the rendered document must not be promoted as a visual confirmed value, even if a PDF text layer or model guess contains it.
- Hidden values can be retained only as review/debug candidates with source/provenance labels.
- The parser must not infer hidden supply/tax/line totals from `quantity * unit_price` or from a document summary total.
- Blank quantity cells stay blank/null and require review; they must not be backsolved from amounts.
- No-price documents must keep currency, totals, unit prices, supply amounts, tax amounts, and line totals null unless a human later confirms them.
- `workflow_metadata.document_quality`, `workflow_metadata.field_provenance`, and normalized review issues should describe crop/visibility risk without changing confirmed/export policy.

## Required checks

The upload regression compares expected visible ground truth against API detail/export JSON and fails on dangerous contamination:

- `row_amount_hidden_do_not_infer`
- `blank_quantity_preservation_failed`
- `no_price_document_amount_blocker`
- `no_price_line_amount_created`
- `exchange_rate_not_total`
- `summary_total_not_line_item`
- `header_row_not_line_item`
- `vendor_sku_not_item_row`
- `review_candidate_leaked_to_export`

WARN is acceptable when the document itself is cropped, blurred, or ambiguous and the issue remains in review rather than confirmed/export data.

## Server smoke examples

Mac local must not run VL/GGUF inference. Use the Linux server or running backend API:

```bash
PYTHONPATH=backend python3 -m app.scripts.run_generated_vl_primary_regression \
  --sample-dir samples/pdf_samples/generated_vl_primary_regression \
  --output-dir /tmp/docuparse_e2e_logs/generated_vl_primary_regression \
  --timeout-seconds 900 \
  --progress
```

For real-company-style mixed image/PDF smoke:

```bash
PYTHONPATH=backend python3 -m app.scripts.run_generated_vl_primary_regression \
  --sample-dir samples/pdf_samples/new/docuparse_real_company_style_samples_10 \
  --output-dir /tmp/docuparse_e2e_logs/real_company_style_10 \
  --timeout-seconds 900 \
  --progress
```

For blurry samples:

```bash
PYTHONPATH=backend python3 -m app.scripts.run_generated_vl_primary_regression \
  --sample-dir samples/pdf_samples/new/docuparse_real_company_style_blurry_samples_10 \
  --output-dir /tmp/docuparse_e2e_logs/real_company_style_blurry_10 \
  --timeout-seconds 900 \
  --progress
```
