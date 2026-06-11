from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.ocr_table_reconstructor import reconstruct_ocr_line_items
from app.services.parser import DocumentParser


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "samples" / "pdf_samples" / "docuparse_image_based_pdf_samples_10"
DEFAULT_FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "image_pdf_samples"
DEFAULT_BACKEND_API_BASE = os.getenv("DOCUPARSE_API_BASE", "http://localhost:8001/api")
DEFAULT_OCR_WORKER_URL = os.getenv("OCR_WORKER_URL", "http://localhost:8010")


EXPECTED_BY_PREFIX: dict[str, dict[str, Any]] = {
    "01": {"document_type": "purchase_order", "vendor_name": "대한정밀부품", "customer_name": "한빛제조", "document_number": "PO-2026-0801", "issue_date": "2026-08-01", "due_date": "2026-08-14", "currency": "KRW", "subtotal": 540000, "tax": 54000, "extracted_amount": 594000, "line_item_count": 4, "line_totals": [330000, 105600, 35200, 123200], "status_expectation": "ready_possible"},
    "02": {"document_type": "quotation", "vendor_name": "한성산업", "customer_name": "미래정밀", "document_number": "QT-2026-0802", "issue_date": "2026-08-02", "due_date": "2026-08-22", "currency": "KRW", "subtotal": 441000, "tax": 44100, "extracted_amount": 485100, "line_item_count": 3, "status_expectation": "needs_review_allowed_for_ambiguous_material"},
    "03": {"document_type": "invoice", "vendor_name": "성진전자부품", "customer_name": "네오팩토리", "document_number": "INV-2026-0803-332", "issue_date": "2026-08-03", "due_date": "2026-09-02", "currency": "KRW", "subtotal": 1460000, "tax": 146000, "extracted_amount": 1606000, "line_item_count": 3, "line_totals": [495000, 847000, 264000], "forbidden_values": ["3.3333333333333335"]},
    "04": {"document_type": "delivery_note", "vendor_name": "대영부품", "customer_name": "오성테크", "document_number": "DN-2026-0804-055", "issue_date": "2026-08-04", "due_date": "2026-08-05", "line_item_count": 4, "amounts_required": False, "status_expectation": "ready_possible"},
    "05": {"document_type": "transaction_statement", "vendor_name": "태성금속", "customer_name": "세진기계", "document_number": "TS-2026-0805-451", "issue_date": "2026-08-05", "currency": "KRW", "extracted_amount": 517000, "line_item_count": 4, "line_totals": [110000, 165000, 176000, 66000], "forbidden_quantities": [0.04, 111.012]},
    "06": {"document_type": "invoice", "vendor_name": "Global Motion Parts LLC", "customer_name": "NeoFactory Korea", "document_number": "INV-US-2026-0806-019", "issue_date": "2026-08-06", "due_date": "2026-09-05", "currency": "USD", "extracted_amount": 508.0, "line_item_count_min": 3},
    "07": {"document_type": "purchase_order", "vendor_name": "신우금속", "customer_name": "제일기계", "document_number": "PO-2026-0807-777", "issue_date": "2026-08-07", "currency": "KRW", "line_item_count_min": 3, "status_expectation": "needs_review_allowed_for_malformed_amount"},
    "08": {"document_type": "quotation", "vendor_name": "한성산업", "customer_name": "제일기계", "document_number": "QT-2026-0808-009", "issue_date": "2026-08-08", "due_date": "2026-08-31", "currency": "KRW", "extracted_amount": 473000, "line_item_count": 2, "status_expectation": "needs_review_for_missing_quantity"},
    "09": {"document_type": "purchase_order", "vendor_name": "대한정밀부품", "customer_name": "한빛제조", "document_number": "PO-2026-0809-MIX-888", "issue_date": "2026-08-09", "due_date": "2026-08-28", "currency": "KRW", "extracted_amount": 403700, "line_item_count": 3, "forbidden_quantities": [7199]},
    "10": {"document_type": "invoice", "vendor_name": "동진부품", "customer_name": "오성테크", "document_number": "INV-2026-0810-LOW", "issue_date": "2026-08-10", "due_date": "2026-09-10", "currency": "KRW", "extracted_amount": 627000, "line_item_count_min": 3, "status_expectation": "needs_review_allowed_for_poor_ocr"},
}


FAILURE_NOTES: dict[str, str] = {
    "03": "Observed failures: PCB Connector/SKU OCR confusions, repeating unit_price 3.3333333333333335, supply/tax/total misselection, AL6061 quantity missing, and item sum mismatch.",
    "05": "Observed failures: vendor/customer/document number missing, USD misclassification, and M8 HEX BOLT quantity not recovered from noisy OCR.",
    "06": "Observed failures: line_items=0, total parsed as 2 USD, and document_number truncated to INV-US.",
    "07": "Observed failures: header fields missing, USD misclassification, malformed amount row, and leading money prefixes in item names.",
    "08": "Observed failures: quotation/invoice profile conflict, missing quote fields, odd OCR title line, and missing quantity row discarded.",
    "09": "Observed failures: total parsed as 389300, Supply Tota! leaked into item_name, quantity 7199, and leading 14400 158400 prefix in item_name.",
    "10": "Observed failures: poor OCR kept some header fields but produced line_items=0; incomplete item candidates must be preserved for review.",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract real OCR fixtures for DocuParse image-based PDF samples.")
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--mode", choices=["api", "worker"], default=os.getenv("DOCUPARSE_FIXTURE_MODE", "api"))
    parser.add_argument("--api-base", default=DEFAULT_BACKEND_API_BASE)
    parser.add_argument("--ocr-worker-url", default=DEFAULT_OCR_WORKER_URL)
    parser.add_argument("--render-dir", type=Path, default=Path(os.getenv("DOCUPARSE_FIXTURE_RENDER_DIR", "/app/uploads/fixture_rendered_pages")))
    parser.add_argument("--cooldown-seconds", type=float, default=float(os.getenv("DOCUPARSE_FIXTURE_COOLDOWN_SECONDS", "1.0")))
    args = parser.parse_args()
    summaries = extract_fixtures(args)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def extract_fixtures(args: argparse.Namespace) -> list[dict[str, Any]]:
    args.fixture_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for pdf_path in sorted(args.sample_dir.glob("*.pdf")):
        prefix = pdf_path.name.split("_", 1)[0]
        started = time.monotonic()
        try:
            if args.mode == "api":
                payload = _process_with_backend_api(pdf_path, args.api_base)
            else:
                payload = _process_with_worker_api(pdf_path, args.ocr_worker_url, args.render_dir)
            payload["processing_time_ms"] = int((time.monotonic() - started) * 1000)
            _write_fixture_set(args.fixture_dir, prefix, pdf_path.name, payload)
            summaries.append(_summary(prefix, pdf_path.name, payload, ok=True))
        except Exception as exc:
            payload = {
                "ocr_text": "",
                "ocr_blocks": [],
                "provider_metadata": {"fixture_extraction_error": str(exc), "mode": args.mode},
                "current_parsed": {},
                "processing_time_ms": int((time.monotonic() - started) * 1000),
            }
            _write_fixture_set(args.fixture_dir, prefix, pdf_path.name, payload, failure=str(exc))
            summaries.append(_summary(prefix, pdf_path.name, payload, ok=False, error=str(exc)))
        time.sleep(max(0.0, args.cooldown_seconds))
    return summaries


def _process_with_backend_api(pdf_path: Path, api_base: str) -> dict[str, Any]:
    document = _upload_document(pdf_path, api_base)
    document_id = document["id"]
    document = _poll_document(document_id, api_base)
    raw_text = document.get("raw_text") or ""
    metadata = document.get("ingestion_metadata") or {}
    raw_blocks = metadata.get("raw_extracted_blocks") or metadata.get("raw_blocks") or []
    if not raw_blocks and raw_text:
        raw_blocks = [{"type": "api_raw_text", "content": raw_text}]
    file_metadata = metadata.get("file_metadata") or {}
    return {
        "ocr_text": raw_text,
        "ocr_blocks": raw_blocks,
        "provider_metadata": {
            **file_metadata,
            "api_document_id": document_id,
            "api_processing_status": document.get("processing_status"),
            "api_extraction_method": document.get("extraction_method"),
            "api_provider_chain": document.get("provider_chain"),
            "api_review_required": document.get("review_required"),
        },
        "current_parsed": document,
    }


def _process_with_worker_api(pdf_path: Path, worker_url: str, render_dir: Path) -> dict[str, Any]:
    image_paths = _render_pdf_pages(pdf_path, render_dir)
    texts: list[str] = []
    blocks: list[dict[str, Any]] = []
    provider_metadata: dict[str, Any] = {
        "ocr_provider_attempted": [],
        "ocr_provider_failed_reason": {},
        "ocr_worker_url_used": worker_url,
        "ocr_fallback_used": False,
    }
    confidences: list[float] = []
    for index, image_path in enumerate(image_paths, start=1):
        response = _call_ocr_worker(image_path, worker_url)
        text = str(response.get("text") or "").strip()
        if text:
            texts.append(f"Page {index}\n{text}")
        blocks.append({"type": "pdf_page_ocr", "page": index, "image_path": str(image_path), "content": text, "table_blocks": response.get("table_blocks") or []})
        provider_metadata["ocr_provider_attempted"].append("ocr_worker_paddleocr")
        provider_metadata["ocr_provider_succeeded"] = response.get("engine_name") or "ocr_worker_paddleocr"
        provider_metadata["ocr_engine"] = response.get("engine_name") or "ocr_worker_paddleocr"
        provider_metadata["ocr_worker_elapsed_ms"] = (provider_metadata.get("ocr_worker_elapsed_ms") or 0) + int(response.get("elapsed_ms") or 0)
        confidences.append(float(response.get("confidence") or 0.0))
    ocr_text = "\n\n".join(texts).strip()
    parsed = DocumentParser().parse(ocr_text, pdf_path.name)
    provider_metadata["ocr_confidence"] = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "ocr_text": ocr_text,
        "ocr_blocks": blocks,
        "provider_metadata": provider_metadata,
        "current_parsed": _jsonable(parsed),
    }


def _upload_document(pdf_path: Path, api_base: str) -> dict[str, Any]:
    boundary = f"----docuparse-{uuid4().hex}"
    file_bytes = pdf_path.read_bytes()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'.encode(),
        b"Content-Type: application/pdf\r\n\r\n",
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/documents/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return _json_request(request)


def _poll_document(document_id: str, api_base: str, timeout_seconds: float = 300.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = _json_request(urllib.request.Request(f"{api_base.rstrip('/')}/documents/{document_id}"))
        if latest.get("processing_status") not in {"uploaded", "queued", "processing"}:
            return latest
        time.sleep(1.0)
    raise TimeoutError(f"Timed out waiting for document {document_id}; latest status={latest.get('processing_status')}")


def _render_pdf_pages(pdf_path: Path, render_dir: Path) -> list[Path]:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF/fitz is required for worker mode PDF rendering") from exc
    render_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(pdf_path)
    image_paths: list[Path] = []
    try:
        for page_index in range(len(document)):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = render_dir / f"{pdf_path.stem}-fixture-page-{page_index + 1}.png"
            pixmap.save(image_path)
            image_paths.append(image_path)
    finally:
        document.close()
    if not image_paths:
        raise RuntimeError(f"No pages rendered for {pdf_path}")
    return image_paths


def _call_ocr_worker(image_path: Path, worker_url: str) -> dict[str, Any]:
    payload = json.dumps({"image_path": str(image_path)}).encode("utf-8")
    request = urllib.request.Request(
        f"{worker_url.rstrip('/')}/ocr",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = _json_request(request)
    if not response.get("ok", True):
        raise RuntimeError(f"OCR worker returned error for {image_path}: {response}")
    return response


def _json_request(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {request.full_url}: {body}") from exc


def _write_fixture_set(fixture_dir: Path, prefix: str, filename: str, payload: dict[str, Any], failure: str | None = None) -> None:
    ocr_text = payload.get("ocr_text") or ""
    blocks = payload.get("ocr_blocks") or []
    parsed = payload.get("current_parsed") or {}
    provider_metadata = payload.get("provider_metadata") or {}
    lines = [line for line in ocr_text.splitlines() if line.strip()]
    candidates = reconstruct_ocr_line_items(lines)
    parser_parsed = DocumentParser().parse(ocr_text, filename) if ocr_text else None
    if parser_parsed is not None:
        parsed = _jsonable(parser_parsed)
    provider_metadata = _jsonable(provider_metadata)
    _write_text(fixture_dir / f"{prefix}_ocr_text.txt", ocr_text)
    _write_json(fixture_dir / f"{prefix}_ocr_blocks.json", blocks)
    _write_json(fixture_dir / f"{prefix}_ocr_lines.json", {
        "lines": lines,
        "reconstructed_candidates": [
            {"item": candidate.item, "confidence": candidate.confidence, "source_line": candidate.source_line}
            for candidate in candidates
        ],
    })
    _write_json(fixture_dir / f"{prefix}_provider_metadata.json", provider_metadata | {"processing_time_ms": payload.get("processing_time_ms")})
    _write_json(fixture_dir / f"{prefix}_current_parsed.json", parsed)
    _write_json(fixture_dir / f"{prefix}_expected.json", EXPECTED_BY_PREFIX.get(prefix, {}))
    notes = [FAILURE_NOTES.get(prefix, "No known failure note recorded.")]
    if provider_metadata.get("ocr_fallback_used"):
        notes.append("OCR fallback was used while extracting this fixture; verify provider metadata before treating it as PaddleOCR baseline.")
    if failure:
        notes.append(f"Fixture extraction failed: {failure}")
    _write_text(fixture_dir / f"{prefix}_failure_notes.md", "\n\n".join(notes) + "\n")


def _summary(prefix: str, filename: str, payload: dict[str, Any], *, ok: bool, error: str | None = None) -> dict[str, Any]:
    parsed = payload.get("current_parsed") or {}
    metadata = payload.get("provider_metadata") or {}
    return {
        "prefix": prefix,
        "filename": filename,
        "ok": ok,
        "error": error,
        "document_type": _value(parsed.get("document_type")),
        "document_number": parsed.get("document_number"),
        "vendor_name": parsed.get("vendor_name"),
        "customer_name": parsed.get("customer_name"),
        "currency": parsed.get("currency"),
        "extracted_amount": parsed.get("extracted_amount"),
        "line_items_count": len(parsed.get("line_items") or []),
        "ocr_provider_succeeded": metadata.get("ocr_provider_succeeded"),
        "ocr_fallback_used": metadata.get("ocr_fallback_used"),
        "processing_time_ms": payload.get("processing_time_ms"),
    }


def _write_text(path: Path, value: str) -> None:
    path.write_text(value or "", encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(inner) for inner in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


if __name__ == "__main__":
    main()
