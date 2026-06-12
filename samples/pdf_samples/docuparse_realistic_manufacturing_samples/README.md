# DocuParse realistic manufacturing samples

Synthetic but realistic Korean manufacturing-office documents for regression testing.

Includes PDFs, plain text companions, item master CSV, and ground_truth.json.

Recommended expected statuses:
- Ready: 11, 14, 20
- Needs Review: 12, 13, 15, 16, 17, 18, 19, 21, 22

Key edge cases:
- option quotation where alternative rows should not be summed
- monthly statement with previous balance
- delivery note with quantities but no prices
- rounding/negative adjustment tax invoice
- USD commercial invoice with exchange rate note
- quantity correction notation
- inspection report / internal transfer that should not be treated as normal ERP purchase/sales
- fax-style O/0 amount noise
- multi-line invoice
