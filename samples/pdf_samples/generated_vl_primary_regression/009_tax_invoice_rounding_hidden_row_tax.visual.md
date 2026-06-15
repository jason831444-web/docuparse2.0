# 009_tax_invoice_rounding_hidden_row_tax

- PDF: `009_tax_invoice_rounding_hidden_row_tax.pdf`
- Text layer expected: `False`
- Visual crop: `True`
- Visible columns: item_name, document_item_code, spec, quantity, unit, unit_price
- Hidden/cropped columns: tax_amount, line_total

## Visual Ground Truth
- document_number: TAX-GEN-2026-009
- total_amount: 296680
- no_price_document: False

## Notes
- Row supply amount for the first item is visually clipped; summary subtotal/tax/total remain visible.