# Docparse Quality Report (smoke-fallback)

- Mode: `fallback`
- Generated at: `2026-04-27T16:16:07.819762`
- Documents: `10`
- Average score: `90.9`
- Status counts: `{'ready': 6, 'needs_review': 4}`
- Severity counts: `{'warn': 13}`

## Top Issue Patterns

- `summary_repetitive`: 6
- `summary_fragment_dump`: 6
- `action_item_weak`: 1

## Most Problematic Documents

- `east_repair_receipt.png` score `79` issues `summary_repetitive, summary_fragment_dump, action_item_weak`
- `workshop_facilitation_memo.md` score `86` issues `summary_repetitive, summary_fragment_dump`
- `harbor_power_statement.html` score `86` issues `summary_repetitive, summary_fragment_dump`
- `faculty_forum_notice.xml` score `86` issues `summary_repetitive, summary_fragment_dump`
- `april_consulting_invoice.csv` score `86` issues `summary_repetitive, summary_fragment_dump`

## Per-Document Results

### `syllabus_system_fundamentals.pdf`

- Score: `100`
- Profile: `syllabus`
- Category: `syllabus`
- Broad type: `document`
- Title: `CSE 320: System Fundamentals II (L01, Stark) Syllabus`
- Provider chain: `pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues: none

### `wolfie_studies_presentation_guide.docx`

- Score: `100`
- Profile: `presentation_guide`
- Category: `presentation_guide`
- Broad type: `memo`
- Title: `Wolfie Studies Presentation Guide`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues: none

### `student_profile_note.txt`

- Score: `100`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `txt_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues: none

### `workshop_facilitation_memo.md`

- Score: `86`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Workshop Facilitation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `east_repair_receipt.png`

- Score: `79`
- Profile: `repair_service_receipt`
- Category: `repair_service_receipt`
- Broad type: `receipt`
- Title: `East Repair Inc. receipt`
- Provider chain: `image_ocr_fast_path+receipt_image_fast_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.
  - [warn] `action_item_weak` Action item is not very user-facing or review-oriented: `Receipt is ready for expense export or filing.`

### `alex_morgan_resume.json`

- Score: `100`
- Profile: `resume_profile`
- Category: `resume_profile`
- Broad type: `document`
- Title: `Alex Morgan`
- Provider chain: `json_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues: none

### `harbor_power_statement.html`

- Score: `86`
- Profile: `utility_bill`
- Category: `utility_bill`
- Broad type: `document`
- Title: `Harbor Power Monthly Statement`
- Provider chain: `html_text_extract+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `faculty_forum_notice.xml`

- Score: `86`
- Profile: `meeting_notice`
- Category: `meeting_notice`
- Broad type: `notice`
- Title: `Faculty Forum Meeting Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `april_consulting_invoice.csv`

- Score: `86`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Invoice Number | INV-2048`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `studio_services_invoice.xlsx`

- Score: `86`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Vendor  Studio North Services receipt`
- Provider chain: `xlsx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

