PDF/OCR readiness fixtures
==========================

This folder contains text fixtures that model extraction outputs from real PDF paths.
They are not sample-specific parser shortcuts. Tests use them with synthetic
NormalizedDocument metadata to verify the general AI escalation policy.

- text_layer_purchase_order.txt: clean text-layer PDF extraction that should stay rule-based.
- scanned_low_ocr_purchase_order.txt: scanned/OCR extraction with low OCR confidence.
- broken_table_invoice.txt: table-damaged PDF extraction with missing line item fields and low table confidence.
