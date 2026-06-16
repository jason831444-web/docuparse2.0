# Docparse Quality Report (gemma-refine1-cleanup)

- Mode: `gemma`
- Generated at: `2026-04-27T18:47:43.571065`
- Documents: `10`
- Average score: `97.2`
- Status counts: `{'ready': 6, 'needs_review': 4}`
- Severity counts: `{'warn': 4}`
- Backend URL: `http://localhost:8001`

## Top Issue Patterns

- `summary_repetitive`: 4

## Most Problematic Documents

- `workshop_facilitation_memo.md` score `93` issues `summary_repetitive`
- `faculty_forum_notice.xml` score `93` issues `summary_repetitive`
- `april_consulting_invoice.csv` score `93` issues `summary_repetitive`
- `studio_services_invoice.xlsx` score `93` issues `summary_repetitive`
- `syllabus_system_fundamentals.pdf` score `100` issues `none`

## Comparison

- Previous mode: `gemma`
- Current mode: `gemma`
- Previous average score: `90.9`
- Current average score: `97.2`
- Delta: `6.3`
- Improved docs: `[{'id': 'instructional_memo_md', 'delta': 7}, {'id': 'repair_receipt_png', 'delta': 21}, {'id': 'utility_bill_html', 'delta': 14}, {'id': 'meeting_notice_xml', 'delta': 7}, {'id': 'invoice_csv', 'delta': 7}, {'id': 'invoice_xlsx', 'delta': 7}]`
- Regressed docs: `[]`

## Per-Document Results

### `syllabus_system_fundamentals.pdf`

- Score: `100`
- Profile: `syllabus`
- Category: `syllabus`
- Broad type: `document`
- Title: `CSE 320: System Fundamentals II (L01, Stark) Syllabus`
- Provider chain: `pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `wolfie_studies_presentation_guide.docx`

- Score: `100`
- Profile: `presentation_guide`
- Category: `presentation_guide`
- Broad type: `memo`
- Title: `Wolfie Studies Presentation Guide`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `student_profile_note.txt`

- Score: `100`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `txt_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `workshop_facilitation_memo.md`

- Score: `93`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Workshop Facilitation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.

### `east_repair_receipt.png`

- Score: `100`
- Profile: `repair_service_receipt`
- Category: `repair_service_receipt`
- Broad type: `receipt`
- Title: `East Repair Inc. receipt`
- Provider chain: `image_ocr_fast_path+receipt_image_fast_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `alex_morgan_resume.json`

- Score: `100`
- Profile: `resume_profile`
- Category: `resume_profile`
- Broad type: `document`
- Title: `Alex Morgan`
- Provider chain: `json_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `harbor_power_statement.html`

- Score: `100`
- Profile: `utility_bill`
- Category: `utility_bill`
- Broad type: `document`
- Title: `Harbor Power Monthly Statement`
- Provider chain: `html_text_extract+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `faculty_forum_notice.xml`

- Score: `93`
- Profile: `meeting_notice`
- Category: `meeting_notice`
- Broad type: `notice`
- Title: `Faculty Forum Meeting Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.

### `april_consulting_invoice.csv`

- Score: `93`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Invoice Number | INV-2048`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.

### `studio_services_invoice.xlsx`

- Score: `93`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Vendor  Studio North Services receipt`
- Provider chain: `xlsx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.

