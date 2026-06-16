# Clear visible PDF samples for Docparse

These 12 synthetic business PDFs are designed to be fully visible when opened/rendered. No table column is intentionally cropped. Each PDF has matching `.expected.json` and `.visual.md` ground truth files.

## Regression intent

This fixture set is the baseline for documents where the user can visually inspect
all meaningful table columns in the rendered PDF. The expected JSON is written
from visible ground truth, not from whatever a model happens to output.

Clear-visible rules:

- Values visible in the rendered PDF should be eligible for confirmed fields,
  confirmed line items, and ERP export after parser/validation gates pass.
- Business policy still applies. An option quote with no final selected option
  must keep the document total unconfirmed. No-price delivery, inspection, and
  internal-transfer documents must not synthesize currency, subtotal, tax, total,
  unit price, supply amount, or line total.
- Exchange-rate notes are not document totals or line amounts.
- Vendor SKU/header labels are not item rows.
- Header, summary, bank, memo, and footer rows must not become line items.
- Return/credit documents may remain review-required because amount direction
  and related-document application are business decisions, even when row amounts
  are visible.

Each no-price quantity document also includes a representative `quantity` field
in addition to domain-specific quantities such as `delivered_quantity`,
`received_quantity`, or `requested_quantity`, so ERP-oriented comparisons can
check both the generic quantity and the operational quantity.

## Cropped / hidden-column regressions are separate

Do not use this fixture's expectations for cropped or hidden-column documents.
Cropped/hidden-column samples must be kept in a separate fixture set with
`visual_crop: true` and explicit `hidden_or_cropped_columns`.

Cropped/hidden-column rules:

- If amount, tax, total, decision, remaining quantity, or note columns are not
  visible in the rendered PDF, those cells must not be promoted as visual
  confirmed/export values.
- Text-layer values that are not visible may be preserved as debug/review
  candidates only when the source is labeled clearly, for example
  `text_layer_source`.
- Hidden row supply/tax/line-total values must not be inferred from
  `quantity * unit_price` just to match a document summary.
- Any hidden or hallucinated value that reaches confirmed ERP export is a
  dangerous contamination and should fail regression.

The zip archive next to this folder is only a transfer artifact. Do not commit
or use the zip file as the regression fixture.
