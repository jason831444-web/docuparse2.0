# 008_statement_text_layer_hidden_total

- PDF: `008_statement_text_layer_hidden_total.pdf`
- Text layer expected: `True`
- Visual crop: `True`
- Visible columns: item_name, spec, quantity, unit, unit_price
- Hidden/cropped columns: supply_amount, tax_amount, line_total

## Visual Ground Truth
- document_number: TS-GEN-2026-008
- total_amount: 705100
- no_price_document: False

## Notes
- Text layer contains hidden row totals; visual confirmed values must not pretend those columns are visible. Row supply amounts are partially clipped at the right edge.