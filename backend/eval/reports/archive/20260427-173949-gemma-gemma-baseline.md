# Docparse Quality Report (gemma-baseline)

- Mode: `gemma`
- Generated at: `2026-04-27T17:39:49.275164`
- Documents: `10`
- Average score: `72.9`
- Status counts: `{'ready': 6, 'needs_review': 4}`
- Severity counts: `{'fail': 10, 'warn': 13}`
- Backend URL: `http://localhost:8001`

## Top Issue Patterns

- `gemma_not_used`: 10
- `summary_repetitive`: 6
- `summary_fragment_dump`: 6
- `action_item_weak`: 1

## Most Problematic Documents

- `east_repair_receipt.png` score `61` issues `gemma_not_used, summary_repetitive, summary_fragment_dump, action_item_weak`
- `workshop_facilitation_memo.md` score `68` issues `gemma_not_used, summary_repetitive, summary_fragment_dump`
- `harbor_power_statement.html` score `68` issues `gemma_not_used, summary_repetitive, summary_fragment_dump`
- `faculty_forum_notice.xml` score `68` issues `gemma_not_used, summary_repetitive, summary_fragment_dump`
- `april_consulting_invoice.csv` score `68` issues `gemma_not_used, summary_repetitive, summary_fragment_dump`

## Per-Document Results

### `syllabus_system_fundamentals.pdf`

- Score: `82`
- Profile: `syllabus`
- Category: `syllabus`
- Broad type: `document`
- Title: `CSE 320: System Fundamentals II (L01, Stark) Syllabus`
- Provider chain: `pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic

### `wolfie_studies_presentation_guide.docx`

- Score: `82`
- Profile: `presentation_guide`
- Category: `presentation_guide`
- Broad type: `memo`
- Title: `Wolfie Studies Presentation Guide`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic

### `student_profile_note.txt`

- Score: `82`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `txt_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: txt_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic

### `workshop_facilitation_memo.md`

- Score: `68`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Workshop Facilitation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `east_repair_receipt.png`

- Score: `61`
- Profile: `repair_service_receipt`
- Category: `repair_service_receipt`
- Broad type: `receipt`
- Title: `East Repair Inc. receipt`
- Provider chain: `image_ocr_fast_path+receipt_image_fast_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: image_ocr_fast_path+receipt_image_fast_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.
  - [warn] `action_item_weak` Action item is not very user-facing or review-oriented: `Receipt is ready for expense export or filing.`

### `alex_morgan_resume.json`

- Score: `82`
- Profile: `resume_profile`
- Category: `resume_profile`
- Broad type: `document`
- Title: `Alex Morgan`
- Provider chain: `json_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: json_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic

### `harbor_power_statement.html`

- Score: `68`
- Profile: `utility_bill`
- Category: `utility_bill`
- Broad type: `document`
- Title: `Harbor Power Monthly Statement`
- Provider chain: `html_text_extract+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: html_text_extract+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `faculty_forum_notice.xml`

- Score: `68`
- Profile: `meeting_notice`
- Category: `meeting_notice`
- Broad type: `notice`
- Title: `Faculty Forum Meeting Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `april_consulting_invoice.csv`

- Score: `68`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Invoice Number | INV-2048`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

### `studio_services_invoice.xlsx`

- Score: `68`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Vendor  Studio North Services receipt`
- Provider chain: `xlsx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic`
- Issues:
  - [fail] `gemma_not_used` Gemma mode was requested, but provider chain shows fallback path: xlsx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_unavailable+interpretation_fallback_heuristic
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `summary_fragment_dump` Detailed summary still feels like ranked fragments pasted into prose.

