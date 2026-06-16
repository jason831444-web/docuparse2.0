# Docparse Quality Report (gemma-harder-refine1)

- Mode: `gemma`
- Generated at: `2026-04-28T19:46:55.546771`
- Documents: `18`
- Average score: `95.06`
- Status counts: `{'ready': 11, 'needs_review': 7}`
- Severity counts: `{'fail': 3, 'warn': 5}`
- Backend URL: `http://127.0.0.1:8001`

## Top Issue Patterns

- `profile_mismatch`: 3
- `category_mismatch`: 3
- `broad_type_mismatch`: 2

## Most Problematic Documents

- `faculty_forum_notice.xml` score `68` issues `profile_mismatch, category_mismatch, broad_type_mismatch`
- `advising_rollout_notice.xml` score `68` issues `profile_mismatch, category_mismatch, broad_type_mismatch`
- `wolfie_studies_presentation_guide.docx` score `75` issues `profile_mismatch, category_mismatch`
- `syllabus_system_fundamentals.pdf` score `100` issues `none`
- `student_profile_note.txt` score `100` issues `none`

## Comparison

- Previous mode: `gemma`
- Current mode: `gemma`
- Previous average score: `92.17`
- Current average score: `95.06`
- Delta: `2.89`
- Improved docs: `[{'id': 'instructional_memo_md', 'delta': 7}, {'id': 'invoice_csv', 'delta': 7}, {'id': 'invoice_xlsx', 'delta': 7}, {'id': 'long_course_guide_pdf', 'delta': 7}, {'id': 'long_policy_memo_md', 'delta': 78}, {'id': 'multirow_invoice_csv', 'delta': 7}]`
- Regressed docs: `[{'id': 'presentation_docx', 'delta': -25}, {'id': 'meeting_notice_xml', 'delta': -25}, {'id': 'nested_meeting_notice_xml', 'delta': -11}]`

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

- Score: `75`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Wolfie Studies Presentation Guide`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [fail] `profile_mismatch` Expected one of ['presentation_guide', 'speaking_notes'], got instructional_memo.
  - [warn] `category_mismatch` Expected category near ['presentation_guide', 'speaking_notes'], got instructional_memo.

### `student_profile_note.txt`

- Score: `100`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `txt_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `workshop_facilitation_memo.md`

- Score: `100`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Workshop Facilitation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

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

- Score: `68`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Faculty Forum Meeting Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [fail] `profile_mismatch` Expected one of ['meeting_notice'], got instructional_memo.
  - [warn] `category_mismatch` Expected category near ['meeting_notice'], got instructional_memo.
  - [warn] `broad_type_mismatch` Expected broad type near ['notice'], got memo.

### `april_consulting_invoice.csv`

- Score: `100`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Invoice Number | INV-2048`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `studio_services_invoice.xlsx`

- Score: `100`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `Vendor  Studio North Services receipt`
- Provider chain: `xlsx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `advanced_data_stewardship_course_guide.pdf`

- Score: `100`
- Profile: `course_guide`
- Category: `course_guide`
- Broad type: `document`
- Title: `IDS 410: Advanced Data Stewardship Course Guide`
- Provider chain: `pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `lab_access_policy_memo.md`

- Score: `100`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Laboratory Access Policy Implementation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `ridgeview_water_statement.html`

- Score: `100`
- Profile: `utility_bill`
- Category: `utility_bill`
- Broad type: `document`
- Title: `Ridgeview Water Authority Statement`
- Provider chain: `html_text_extract+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `samira_chen_product_data_resume.json`

- Score: `100`
- Profile: `resume_profile`
- Category: `resume_profile`
- Broad type: `document`
- Title: `Samira Chen`
- Provider chain: `json_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `noisy_bicycle_repair_bill.png`

- Score: `100`
- Profile: `repair_service_receipt`
- Category: `repair_service_receipt`
- Broad type: `receipt`
- Title: `Acct BC-4491 1 Ticket WO-7718 receipt`
- Provider chain: `image_ocr_fast_path+receipt_image_fast_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `advising_rollout_notice.xml`

- Score: `68`
- Profile: `instructional_memo`
- Category: `instructional_memo`
- Broad type: `memo`
- Title: `Advising Workflow Rollout Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [fail] `profile_mismatch` Expected one of ['meeting_notice'], got instructional_memo.
  - [warn] `category_mismatch` Expected category near ['meeting_notice'], got instructional_memo.
  - [warn] `broad_type_mismatch` Expected broad type near ['notice', 'document'], got memo.

### `campus_media_services_invoice.csv`

- Score: `100`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `VendorCampus Media Services receipt`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

### `mentor_program_participant_profile.docx`

- Score: `100`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

