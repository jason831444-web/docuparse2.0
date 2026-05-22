from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import requests


DEMO_FILES = [
    "syllabus_system_fundamentals.pdf",
    "east_repair_receipt.png",
    "studio_services_invoice.xlsx",
    "lab_access_policy_memo.md",
    "harbor_power_statement.html",
    "faculty_forum_notice.xml",
    "alex_morgan_resume.json",
    "student_profile_note.txt",
    "workshop_facilitation_memo.md",
]

DONE_STATUSES = {"ready", "needs_review", "confirmed", "failed"}


def upload_file(api_base: str, path: Path, timeout: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        response = requests.post(
            f"{api_base}/documents/upload",
            files={"file": (path.name, handle)},
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json()


def wait_for_document(api_base: str, document_id: str, poll_timeout: int) -> dict[str, Any]:
    deadline = time.time() + poll_timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        response = requests.get(f"{api_base}/documents/{document_id}", timeout=20)
        response.raise_for_status()
        latest = response.json()
        if latest.get("processing_status") in DONE_STATUSES:
            return latest
        time.sleep(2)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload safe sample documents for DocuParse portfolio screenshots.")
    parser.add_argument("--api-base", default="http://localhost:8001/api", help="Backend API base URL.")
    parser.add_argument("--corpus-dir", default="backend/eval/corpus", help="Path to the sample document corpus.")
    parser.add_argument("--limit", type=int, default=0, help="Upload only the first N demo files.")
    parser.add_argument("--upload-timeout", type=int, default=900, help="Per-upload timeout in seconds.")
    parser.add_argument("--poll-timeout", type=int, default=900, help="Per-document processing wait timeout in seconds.")
    parser.add_argument("--no-wait", action="store_true", help="Upload files without polling for processing completion.")
    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir)
    files = DEMO_FILES[: args.limit] if args.limit else DEMO_FILES
    created: list[dict[str, Any]] = []

    for filename in files:
        path = corpus_dir / filename
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 1
        print(f"uploading {path.name}...")
        document = upload_file(args.api_base, path, args.upload_timeout)
        if not args.no_wait:
            document = wait_for_document(args.api_base, document["id"], args.poll_timeout)
        created.append(document)
        print(
            f"  {document.get('processing_status')} | "
            f"{document.get('category') or 'uncategorized'} | "
            f"{document.get('title') or document.get('original_filename')}"
        )

    print("\nUploaded demo documents:")
    for document in created:
        print(f"- {document['id']} {document.get('category') or 'uncategorized'} {document.get('original_filename')}")
    print("\nOpen the frontend Categories page and Document Detail pages for screenshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
