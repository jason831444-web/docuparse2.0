from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a compact PASS/WARN/FAIL report from DocuParse E2E regression logs.")
    parser.add_argument("--log", action="append", type=Path, required=True, help="E2E log produced by run_image_pdf_sample_regression. Can be repeated.")
    parser.add_argument("--ground-truth", action="append", type=Path, default=[], help="Optional ground_truth.json file. Can be repeated.")
    parser.add_argument("--output-json", type=Path, default=Path("/tmp/docuparse_e2e_logs/combined_regression_report.json"))
    parser.add_argument("--output-md", type=Path, default=Path("/tmp/docuparse_e2e_logs/combined_regression_report.md"))
    args = parser.parse_args()

    truth = _load_ground_truth(args.ground_truth)
    rows: list[dict[str, Any]] = []
    for log_path in args.log:
        for result in _load_log_results(log_path):
            rows.append(_compare_result(result, truth.get(_truth_key(result.get("filename") or "")), source=str(log_path)))

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(_markdown_report(rows), encoding="utf-8")
    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_md}")


def _load_ground_truth(paths: list[Path]) -> dict[str, dict[str, Any]]:
    truth: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data if isinstance(data, list) else data.values():
            filename = str(entry.get("file") or entry.get("filename") or "")
            expected = entry.get("expected") or entry
            if filename:
                truth[_truth_key(filename)] = expected
                # Photo and text-layer sets share the same logical names except
                # for the real/photo prefix.
                truth[_truth_key(filename.replace("_real_", "_photo_"))] = expected
                truth[_truth_key(filename.replace("_photo_", "_real_"))] = expected
    return truth


def _truth_key(filename: str) -> str:
    return re.sub(r"^(?:\d+_)?(?:real|photo)_", "", Path(filename).name.lower())


def _load_log_results(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    marker = "\nJSON\n"
    if marker in text:
        payload = text.rsplit(marker, 1)[1].strip()
        return json.loads(payload)
    start = text.rfind("\n[")
    if start >= 0:
        return json.loads(text[start + 1:].strip())
    raise ValueError(f"Could not find JSON result array in {path}")


def _compare_result(result: dict[str, Any], expected: dict[str, Any] | None, *, source: str) -> dict[str, Any]:
    reasons: list[str] = []
    status = "PASS"
    expected = expected or {}

    def check(field: str, actual_key: str, expected_key: str | None = None) -> None:
        nonlocal status
        expected_value = expected.get(expected_key or field)
        actual_value = result.get(actual_key)
        if expected_value in (None, "", []):
            return
        if field == "type" and _type_covered_by_taxonomy(str(expected_value), str(actual_value or ""), result):
            reasons.append(f"type: expected {expected_value}, represented by subtype/profile on {actual_value}")
            if status == "PASS":
                status = "WARN"
            return
        if str(expected_value) != str(actual_value):
            reasons.append(f"{field}: expected {expected_value}, got {actual_value}")
            status = "FAIL"

    check("type", "document_type", "document_type")
    check("doc_no", "document_number", "document_number")
    check("currency", "currency", "currency")

    expected_total = expected.get("total_amount")
    actual_total = result.get("extracted_amount")
    if expected_total not in (None, "", []):
        if _decimal(expected_total) != _decimal(actual_total):
            if _is_return_taxonomy(result):
                reasons.append(f"total: expected {expected_total}, got {actual_total}; return/credit amount sign requires review")
                if status == "PASS":
                    status = "WARN"
            else:
                reasons.append(f"total: expected {expected_total}, got {actual_total}")
                status = "FAIL"
    elif _expects_no_amount(expected) and actual_total not in (None, "", []):
        reasons.append(f"total: expected no amount for no-price document, got {actual_total}")
        status = "FAIL"

    expected_count = expected.get("line_items")
    actual_count = result.get("line_items_count")
    if expected_count not in (None, "", []):
        if actual_count is None or int(actual_count) < int(expected_count):
            reasons.append(f"line_items_count: expected at least {expected_count}, got {actual_count}")
            status = "WARN" if status == "PASS" else status

    if result.get("error"):
        reasons.append(str(result["error"]))
        status = "FAIL"
    review_reasons = _review_reason_counts(result.get("review_reasons") or [])

    return {
        "filename": result.get("filename"),
        "expected_type": expected.get("document_type"),
        "actual_type": result.get("document_type"),
        "actual_subtype": result.get("document_subtype"),
        "actual_profile": result.get("document_profile"),
        "actual_profiles": result.get("document_profiles") or [],
        "layout_profile": result.get("layout_profile"),
        "expected_doc_no": expected.get("document_number"),
        "actual_doc_no": result.get("document_number"),
        "expected_total": expected.get("total_amount"),
        "actual_total": result.get("extracted_amount"),
        "expected_currency": expected.get("currency"),
        "actual_currency": result.get("currency"),
        "expected_line_items_count": expected_count,
        "actual_line_items_count": actual_count,
        "processing_status": result.get("processing_status"),
        "review_required": result.get("review_required"),
        "review_reasons": result.get("review_reasons") or [],
        "review_reason_summary": review_reasons,
        "provider_used": result.get("provider_used") or result.get("ocr_provider_succeeded"),
        "primary_provider": result.get("primary_provider"),
        "fallback_provider": result.get("fallback_provider"),
        "fallback_used": result.get("ocr_fallback_used"),
        "fallback_reason": _compact_json(result.get("fallback_reason")),
        "api_provider_chain": result.get("api_provider_chain"),
        "line_candidates_count": result.get("line_candidates_count"),
        "review_candidates_count": result.get("review_candidates_count"),
        "processing_time_ms": result.get("processing_time_ms"),
        "status": status,
        "reasons": reasons,
        "source": source,
    }


def _type_covered_by_taxonomy(expected_type: str, actual_type: str, result: dict[str, Any]) -> bool:
    expected = _normalize_type(expected_type)
    actual = _normalize_type(actual_type)
    subtype = _normalize_type(result.get("document_subtype"))
    profile = _normalize_type(result.get("document_profile"))
    profiles = {_normalize_type(value) for value in (result.get("document_profiles") or [])}
    profiles.discard("")
    if expected == "tax_invoice":
        return actual == "invoice" and (subtype == "tax_invoice" or profile == "tax_document" or "tax_document" in profiles)
    if expected in {"return_note", "credit_note", "return_credit_note"}:
        return actual == "general_document" and (
            subtype in {"return_note", "credit_note", "return_credit_note"}
            or profile == "return_document"
            or "return_document" in profiles
        )
    if expected == "internal_transfer":
        return actual == "general_document" and (
            subtype == "internal_transfer"
            or profile == "inventory_movement_document"
            or "inventory_movement_document" in profiles
        )
    return False


def _is_return_taxonomy(result: dict[str, Any]) -> bool:
    subtype = _normalize_type(result.get("document_subtype"))
    profile = _normalize_type(result.get("document_profile"))
    profiles = {_normalize_type(value) for value in (result.get("document_profiles") or [])}
    return subtype in {"return_note", "credit_note", "return_credit_note"} or profile == "return_document" or "return_document" in profiles


def _expects_no_amount(expected: dict[str, Any]) -> bool:
    return _normalize_type(expected.get("document_type")) in {"delivery_note", "inspection_report", "internal_transfer"}


def _normalize_type(value: Any) -> str:
    return re.sub(r"[\s/-]+", "_", str(value or "").strip().lower())


def _decimal(value: Any) -> Decimal | None:
    try:
        if value in (None, "", []):
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _review_reason_counts(values: list[Any]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{key} x{count}" if count > 1 else key for key, count in sorted(counts.items()))


def _markdown_report(rows: list[dict[str, Any]]) -> str:
    summary = _report_summary(rows)
    lines = [
        "# DocuParse E2E Regression Report",
        "",
        "Review Reasons may include non-blocking informational codes; use Status and Review Required for pass/fail gating.",
        "",
        "## Operational Summary",
        "",
        f"- Documents: {summary['total']}",
        f"- PASS/WARN/FAIL: {summary['pass']} / {summary['warn']} / {summary['fail']}",
        f"- Review Required: {summary['review_required']} ({summary['review_required_ratio']}%)",
        f"- Processing Statuses: {summary['processing_statuses'] or '-'}",
        f"- Top Review Signals: {summary['top_review_signals'] or '-'}",
        "",
        "## Document Results",
        "",
        "| Status | Processing | Review Required | Filename | Type | Subtype | Profile | Doc No | Total | Currency | Items | Provider | Fallback | Candidates | Review Reasons | Report Reasons |",
        "|---|---|---:|---|---|---|---|---|---:|---|---:|---|---|---:|---|---|",
    ]
    for row in rows:
        reasons = "<br>".join(row.get("reasons") or [])
        values = {key: row.get(key) if row.get(key) is not None else "" for key in row}
        values["reasons"] = reasons
        lines.append(
            "| {status} | {processing_status} | {review_required} | {filename} | {actual_type} | {actual_subtype} | {actual_profile} | {actual_doc_no} | {actual_total} | {actual_currency} | {actual_line_items_count} | {provider_used} | {fallback_reason} | {review_candidates_count} | {review_reason_summary} | {reasons} |".format(
                **values,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _report_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    processing_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    review_required = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
        processing = str(row.get("processing_status") or "unknown")
        processing_counts[processing] = processing_counts.get(processing, 0) + 1
        if row.get("review_required"):
            review_required += 1
        for reason in row.get("review_reasons") or []:
            key = str(reason or "").strip()
            if key:
                reason_counts[key] = reason_counts.get(key, 0) + 1
    total = len(rows)
    processing_summary = ", ".join(f"{key} x{value}" for key, value in sorted(processing_counts.items()))
    top_reasons = ", ".join(
        f"{key} x{value}" for key, value in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    )
    return {
        "total": total,
        "pass": status_counts["PASS"],
        "warn": status_counts["WARN"],
        "fail": status_counts["FAIL"],
        "review_required": review_required,
        "review_required_ratio": round((review_required / total * 100) if total else 0, 1),
        "processing_statuses": processing_summary,
        "top_review_signals": top_reasons,
    }


def _compact_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
