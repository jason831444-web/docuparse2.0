# Docparse Quality Report (gemma-harder-baseline)

- Mode: `gemma`
- Generated at: `2026-04-28T19:15:57.966361`
- Documents: `18`
- Average score: `92.17`
- Status counts: `{'ready': 11, 'needs_review': 7}`
- Severity counts: `{'warn': 15, 'fail': 2}`
- Backend URL: `http://localhost:8001`

## Top Issue Patterns

- `summary_repetitive`: 7
- `action_item_weak`: 2
- `generic_profile`: 1
- `profile_mismatch`: 1
- `category_mismatch`: 1
- `broad_type_mismatch`: 1
- `summary_generic`: 1
- `generic_profile_specific_category`: 1
- `gemma_output_fallback_like`: 1
- `action_item_too_long`: 1

## Most Problematic Documents

- `lab_access_policy_memo.md` score `22` issues `generic_profile, profile_mismatch, category_mismatch, broad_type_mismatch, summary_generic, summary_repetitive, generic_profile_specific_category, gemma_output_fallback_like`
- `advising_rollout_notice.xml` score `79` issues `summary_repetitive, action_item_too_long, action_item_weak`
- `workshop_facilitation_memo.md` score `93` issues `summary_repetitive`
- `faculty_forum_notice.xml` score `93` issues `summary_repetitive`
- `april_consulting_invoice.csv` score `93` issues `summary_repetitive`

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

### `advanced_data_stewardship_course_guide.pdf`

- Score: `93`
- Profile: `course_guide`
- Category: `course_guide`
- Broad type: `document`
- Title: `IDS 410: Advanced Data Stewardship Course Guide`
- Provider chain: `pdf_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `action_item_weak` Action item is not very user-facing or review-oriented: `Communication.`

### `lab_access_policy_memo.md`

- Score: `22`
- Profile: `generic_document`
- Category: `education`
- Broad type: `notice`
- Title: `Laboratory Access Policy Implementation Memo`
- Provider chain: `md_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [fail] `generic_profile` Document stayed generic even though stronger evidence was expected.
  - [fail] `profile_mismatch` Expected one of ['instructional_memo'], got generic_document.
  - [warn] `category_mismatch` Expected category near ['instructional_memo'], got education.
  - [warn] `broad_type_mismatch` Expected broad type near ['memo', 'document'], got notice.
  - [warn] `summary_generic` Detailed summary still sounds generic.
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `generic_profile_specific_category` Category is specific but profile stayed generic.
  - [warn] `gemma_output_fallback_like` Gemma was requested, but the final output still feels too fallback-like or under-explained.

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

- Score: `79`
- Profile: `meeting_notice`
- Category: `meeting_notice`
- Broad type: `notice`
- Title: `Advising Workflow Rollout Notice`
- Provider chain: `xml_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.
  - [warn] `action_item_too_long` Action item is too long: `Required Attendees: undergraduate program directors, advising coordinators, regi...`
  - [warn] `action_item_weak` Action item is not very user-facing or review-oriented: `Required Attendees: undergraduate program directors, advising coordinators, registrar liaison, and department schedulers.`

### `campus_media_services_invoice.csv`

- Score: `93`
- Profile: `invoice`
- Category: `invoice`
- Broad type: `document`
- Title: `VendorCampus Media Services receipt`
- Provider chain: `csv_direct+structured_text_path+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues:
  - [warn] `summary_repetitive` Detailed summary repeats concepts or phrasing in a mechanical way.

### `mentor_program_participant_profile.docx`

- Score: `100`
- Profile: `profile_record`
- Category: `profile_record`
- Broad type: `document`
- Title: `Profile Note`
- Provider chain: `docx_text_extract+heuristic_fallback+heuristic_interpretation+ai_interpretation_gemma_fallback_small+ai_summary_refinement`
- Issues: none

