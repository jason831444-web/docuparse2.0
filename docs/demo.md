# DocuParse Demo Guide

Use this guide to record a short portfolio demo or walk an interviewer through the project.

## Demo Setup

Start the local stack:

```bash
docker compose --profile backend up --build
```

Open:

- Frontend: http://localhost:3001
- API docs: http://localhost:8001/docs

For local GGUF interpretation, make sure this file exists before starting the backend:

```text
models/gguf/gemma-3-4b-it-q4_0.gguf
```

## Suggested Sample Documents

Safe sample documents live in:

```text
backend/eval/corpus/
```

Good demo cases:

- `syllabus_system_fundamentals.pdf`: PDF course guide/category extraction.
- `east_repair_receipt.png`: image OCR and receipt extraction.
- `studio_services_invoice.xlsx`: spreadsheet/Office extraction.
- `lab_access_policy_memo.md`: workflow summary and action-item generation.
- `student_profile_note.txt`: profile-record classification.

For the real-world failure cases that motivated recent improvements, use:

- an installation/setup guide PDF with a person-name-like line
- an implementation schedule spreadsheet containing an endpoint like `/students/{id}` or a task title containing `profile`

The expected behavior is that structure and purpose win over isolated words.

## Screenshot Prep Data

For richer portfolio screenshots, upload a curated set of safe sample documents from the eval corpus:

```bash
cd /Users/yoonjaeseong/Desktop/projects/DocuParse
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/prepare_portfolio_demo.py
```

Useful options:

```bash
# Upload a smaller slice if local GGUF processing is slow.
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/prepare_portfolio_demo.py --limit 4

# Upload without waiting for processing to finish.
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/prepare_portfolio_demo.py --no-wait
```

This script does not create fake production data. It uploads the existing safe sample corpus through the real API so the Categories, Review, Notifications, and Document Detail screens are populated by the same processing path used in the app.

For the 3rd screenshot, open:

```text
http://localhost:3001/categories
```

For the 4th screenshot, open a processed document detail page and crop around the workflow assistant plus correction workspace:

```text
http://localhost:3001/documents/<document-id>
```

## Short Demo Script

1. **Dashboard**
   Show the upload dropzone, status cards, recent documents, review queue, and category folders.

2. **Upload**
   Upload a sample PDF, image receipt, or spreadsheet.

3. **Processing Result**
   Open the document detail page. Point out:
   - original document preview/link
   - extracted text
   - AI result tab
   - provider chain
   - workflow panel
   - review-required warning if present

4. **Category Workflow**
   Change the category with the selector and save. Return to the document list or category page and show that category filtering/search still finds the document.

5. **Review Flow**
   Mark a document as needs review, open the review queue, then confirm it.

6. **Notifications**
   Open notifications and show processed/review/failed/processing events.

7. **Evaluation Evidence**
   Open an evaluation report:

   ```text
   backend/eval/reports/latest-gemma.md
   ```

   Explain that the harness is used to catch regressions in title selection, category interpretation, summaries, action items, and provider-chain visibility.

## Screenshot Checklist

Add screenshots to a portfolio page or GitHub README later:

- Dashboard after several sample uploads
- Upload in progress or processed result
- Document detail page with provider chain visible
- Extracted text tab
- AI result tab
- Category selector
- Category folders page
- Review queue
- Notifications page
- Evaluation report snippet

Suggested screenshot folder:

```text
docs/screenshots/
```

## Example Outcomes To Highlight

### Installation Guide PDF

Expected:

- category: `installation_guide`
- title: setup/installation/manual title, not a person-name line
- workflow: review prerequisites, configuration, commands, environment values
- provider chain: shows PDF extraction and interpretation path

Why it matters:

This demonstrates that the classifier looks at document purpose and structure, not just isolated profile/name tokens.

### Implementation Schedule Spreadsheet

Expected:

- category: `implementation_schedule`
- title: sheet name, filename, or schedule/tracker heading
- action items: review open tasks, ownership, testing, coverage, pipeline status
- filtering/search: still works after category edits

Why it matters:

This demonstrates spreadsheet-aware interpretation and category normalization consistency.

## What To Say In An Interview

Short version:

> I built DocuParse as a local-first AI document workspace. The interesting part is the pipeline: it normalizes many file types, routes documents through lightweight or heavier extraction paths, combines deterministic parsing with optional local GGUF interpretation, records provider-chain provenance, and gives users a review loop instead of blindly trusting AI output.

Good follow-up topics:

- why local GGUF inference was useful
- how provider-chain visibility helped debug failures
- how category normalization fixed search/filter consistency
- what would be needed for production: auth, background jobs, object storage, full-text search, persisted notifications

## Known Demo Caveats

- First GGUF inference can be slow on CPU.
- Auth pages are not wired to real accounts; present this as a local-first workspace.
- Inline processing is fine for the demo but would become a background job in production.
- Generated eval scores are useful regression signals, but real document examples are more persuasive.
