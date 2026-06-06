from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from typing import Any


TERMINAL_STATUSES = {"ready", "needs_review", "failed", "confirmed", "completed"}


def _read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return payload


def _latest_document(api_base: str) -> dict[str, Any]:
    payload = _read_json(f"{api_base.rstrip('/')}/documents?page=1&page_size=1&sort_by=created_at&order=desc")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("No documents found.")
    item = items[0]
    if not isinstance(item, dict):
        raise RuntimeError("Malformed document list response.")
    return item


def _get_document(api_base: str, document_id: str | None) -> dict[str, Any]:
    if document_id:
        return _read_json(f"{api_base.rstrip('/')}/documents/{document_id}")
    latest = _latest_document(api_base)
    latest_id = latest.get("id")
    if not latest_id:
        raise RuntimeError("Latest document does not include an id.")
    return _read_json(f"{api_base.rstrip('/')}/documents/{latest_id}")


def _wait_for_terminal(api_base: str, document_id: str | None, timeout_seconds: float, poll_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_doc: dict[str, Any] | None = None
    while time.time() <= deadline:
        last_doc = _get_document(api_base, document_id)
        if str(last_doc.get("processing_status") or "") in TERMINAL_STATUSES:
            return last_doc
        time.sleep(poll_seconds)
    if last_doc is None:
        raise RuntimeError("Document was not available before timeout.")
    return last_doc


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    ingestion = document.get("ingestion_metadata") if isinstance(document.get("ingestion_metadata"), dict) else {}
    file_metadata = ingestion.get("file_metadata") if isinstance(ingestion.get("file_metadata"), dict) else {}
    return {
        "id": document.get("id"),
        "original_filename": document.get("original_filename"),
        "processing_status": document.get("processing_status"),
        "document_type": document.get("document_type"),
        "ocr_engine": file_metadata.get("ocr_engine"),
        "ocr_provider_attempted": file_metadata.get("ocr_provider_attempted"),
        "ocr_provider_succeeded": file_metadata.get("ocr_provider_succeeded"),
        "ocr_provider_failed_reason": file_metadata.get("ocr_provider_failed_reason"),
        "ocr_worker_url_used": file_metadata.get("ocr_worker_url_used"),
        "ocr_worker_elapsed_ms": file_metadata.get("ocr_worker_elapsed_ms"),
        "ocr_worker_available": file_metadata.get("ocr_worker_available"),
        "ocr_fallback_used": file_metadata.get("ocr_fallback_used"),
        "line_items_count": len(document.get("line_items") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print OCR metadata for the latest or selected DocuParse document.")
    parser.add_argument("--api-base", default=os.getenv("DOCUPARSE_API_BASE", "http://localhost:8001/api"))
    parser.add_argument("--document-id", default=os.getenv("DOCUPARSE_DOCUMENT_ID"))
    parser.add_argument("--wait", action="store_true", help="Wait until the document leaves queued/processing status.")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DOCUPARSE_METADATA_TIMEOUT", "240")))
    parser.add_argument("--poll", type=float, default=float(os.getenv("DOCUPARSE_METADATA_POLL_SECONDS", "5")))
    args = parser.parse_args()

    document = _wait_for_terminal(args.api_base, args.document_id, args.timeout, args.poll) if args.wait else _get_document(args.api_base, args.document_id)
    print(json.dumps(_summary(document), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
