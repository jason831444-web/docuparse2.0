# Manufacturing Regression V1 Visual Ground Truth

These samples are selected from broader smoke pools. The target is not to maximize automatic `ready` results. The target is safe, useful manufacturing business data.

## Expected Policies

- **Visible values**: visible item names, specs, quantities, unit prices, supply amounts, tax amounts, and totals may be confirmed only when the source image visibly supports them.
- **Hidden/cropped values**: values outside the visible render, especially right-side amount/tax/total/decision columns, must stay null/review/debug candidate.
- **No-price documents**: delivery notes, inspection reports, and internal transfers must not invent currency or amount fields.
- **Handwritten/photo documents**: may produce usable item candidates, but should remain review-first unless confidence and field evidence are strong.
- **Blurred documents**: should preserve safe visible data and add quality review flags; no dangerous confirmed contamination is allowed.
- **Return/credit documents**: row amounts may be visible, but sign direction and related-document policy can remain review-required.

## Representative Coverage

- `MFG-001` purchase order, photographed but full visible.
- `MFG-002` tax invoice, photographed but full visible.
- `MFG-003` no-price delivery note, photographed full visible.
- `MFG-004` quotation, photographed/PDF path.
- `MFG-005` incoming inspection report, no-price quality document.
- `MFG-006` transaction statement.
- `MFG-007` return/credit document.
- `MFG-008` internal transfer/no-price movement.
- `MFG-009` blurry purchase order.
- `MFG-010` blurry no-price delivery note.
- `MFG-011` handwritten transaction statement.
- `MFG-012` handwritten material list.
- `MFG-013` cropped no-price delivery note with hidden right columns.
- `MFG-014` commercial invoice with hidden amount column and exchange-rate risk.
