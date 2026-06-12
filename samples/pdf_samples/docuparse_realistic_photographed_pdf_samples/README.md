DocuParse realistic photographed PDF samples

This package contains image-based PDF versions of the realistic manufacturing samples.
Each PDF page is a raster image embedded inside the PDF, styled like a phone/scanner photo:
- slight rotation or perspective
- desk/background shadow
- mild blur/noise/JPEG compression
- some fax/low-light variants

Use these to test OCR/PaddleOCR worker behavior, upload queue durability, review dashboard, and parser robustness for real-world photographed documents.

Folders:
- pdfs/: photographed image-based PDFs
- txt/: original reference text from the clean source samples
- item_master_realistic.csv: item master for matching tests
- ground_truth.json: expected high-level outcomes inherited from the clean realistic samples

Note: These are synthetic test samples, not official business documents.
