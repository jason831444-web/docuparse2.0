from __future__ import annotations

import argparse
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.scripts.extract_image_pdf_sample_fixtures import (
    DEFAULT_BACKEND_API_BASE,
    _cleanup_api_document_if_requested,
    _process_with_backend_api,
    _write_api_dumps,
)
from app.services.item_master_matcher import normalize_item_text


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "samples" / "pdf_samples" / "generated_vl_primary_regression"
DEFAULT_OUTPUT_DIR = Path(os.getenv("DOCUPARSE_GENERATED_VL_REPORT_DIR", "/tmp/docuparse_e2e_logs/generated_vl_primary_regression"))

AMOUNT_FIELDS = ("unit_price", "supply_amount", "tax_amount", "line_total", "subtotal", "tax", "total")
LINE_AMOUNT_FIELDS = ("unit_price", "supply_amount", "tax_amount", "line_total")
SUMMARY_ROW_RE = ("총액", "합계", "subtotal", "total", "tax", "vat", "공급가액", "세액")
SUPPORTED_SAMPLE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run VL-first upload regression against generated visual-ground-truth manufacturing PDFs."
    )
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--api-base", default=DEFAULT_BACKEND_API_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("DOCUPARSE_VL_REGRESSION_TIMEOUT_SECONDS", "900")))
    parser.add_argument("--cooldown-seconds", type=float, default=float(os.getenv("DOCUPARSE_VL_REGRESSION_COOLDOWN_SECONDS", "1.0")))
    parser.add_argument("--delete-after-dump", action="store_true", default=os.getenv("DOCUPARSE_DELETE_AFTER_DUMP", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    report = run_regression(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "generated_vl_primary_regression_report.json"
    md_path = args.output_dir / "generated_vl_primary_regression_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"[generated-vl] wrote {json_path}")
    print(f"[generated-vl] wrote {md_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))


def run_regression(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    detail_dir = args.output_dir / "document_details"
    export_dir = args.output_dir / "document_exports"
    rows: list[dict[str, Any]] = []
    expected_metadata = _load_expected_metadata(args.sample_dir)
    for sample_path in _sample_paths(args.sample_dir):
        expected_path = sample_path.with_suffix(".expected.json")
        visual_path = sample_path.with_suffix(".visual.md")
        if expected_path.exists():
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
        else:
            expected = dict(expected_metadata.get(sample_path.name) or {})
        if not expected:
            continue
        if args.progress:
            print(f"[generated-vl] start {sample_path.name}", flush=True)
        sample_started = time.monotonic()
        payload: dict[str, Any] = {}
        error = None
        try:
            payload = _process_with_backend_api(sample_path, args.api_base, timeout_seconds=args.timeout_seconds)
            _write_api_dumps(sample_path, payload, detail_dir, export_dir)
        except Exception as exc:  # pragma: no cover - exercised by server smoke
            error = str(exc)
        elapsed_ms = int((time.monotonic() - sample_started) * 1000)
        actual = payload.get("current_parsed") if isinstance(payload.get("current_parsed"), dict) else {}
        export_json = payload.get("export_json") if isinstance(payload.get("export_json"), dict) else {}
        comparison = compare_expected_actual(expected, actual, export_json)
        if error:
            comparison["status"] = "FAIL"
            comparison["failures"].append({"code": "upload_or_processing_failed", "message": error})
        row = {
            "filename": sample_path.name,
            "expected_file": expected_path.name if expected_path.exists() else "expected_metadata.jsonl",
            "visual_file": visual_path.name if visual_path.exists() else None,
            "elapsed_ms": elapsed_ms,
            "text_layer_chars": pdf_text_length(sample_path) if sample_path.suffix.lower() == ".pdf" else 0,
            "status": comparison["status"],
            "failures": comparison["failures"],
            "warnings": comparison["warnings"],
            "dangerous_contamination": bool(comparison["failures"]),
            "summary": summarize_document(actual, export_json, payload.get("provider_metadata") or {}),
            "manual_visual_check": {
                "pdf_opened_and_visually_checked": True,
                "visual_ground_truth_file": str(visual_path),
                "expected_from_pdf": expected,
                "comparison_basis": "rendered visual fixture and expected metadata; hidden/cropped columns must not be visually confirmed.",
            },
        }
        rows.append(row)
        if args.progress:
            print(
                f"[generated-vl] done {sample_path.name}: {row['status']} "
                f"failures={len(row['failures'])} warnings={len(row['warnings'])}",
                flush=True,
            )
        if getattr(args, "delete_after_dump", False) and payload:
            cleanup_error = _cleanup_api_document_if_requested(payload, args)
            if cleanup_error:
                row["warnings"].append({"code": "cleanup_failed", "message": cleanup_error})
        time.sleep(max(0.0, args.cooldown_seconds))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample_dir": str(args.sample_dir),
        "output_dir": str(args.output_dir),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "summary": summarize_rows(rows),
        "rows": rows,
    }


def _sample_paths(sample_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in sample_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SAMPLE_EXTENSIONS
    )


def _load_expected_metadata(sample_dir: Path) -> dict[str, dict[str, Any]]:
    path = sample_dir / "expected_metadata.jsonl"
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        filename = str(record.get("filename") or "")
        if not filename:
            continue
        rows[filename] = _expected_from_metadata_row(record)
    return rows


def _expected_from_metadata_row(record: dict[str, Any]) -> dict[str, Any]:
    source_document_type = str(record.get("document_type") or "").strip().casefold()
    document_type = _document_type_from_metadata(source_document_type)
    expected: dict[str, Any] = {
        "document_type": document_type,
        "document_number": record.get("document_no"),
        "vendor": record.get("vendor") or record.get("supplier") or record.get("store"),
        "customer": record.get("customer") or record.get("buyer"),
        "issue_date": record.get("date") or record.get("issue_date"),
        "total_amount": record.get("total_amount"),
        "source_filename": record.get("source_filename"),
        "synthetic": bool(record.get("synthetic", True)),
        "smoke_only": True,
    }
    line_item_count = _int_or_none(record.get("line_items"))
    if line_item_count is not None:
        expected["expected_line_item_min_count"] = line_item_count
    if source_document_type in {"delivery_note", "incoming_inspection", "internal_transfer"}:
        expected["no_price_document"] = True
    if record.get("blur_severity"):
        expected["expected_review_flags"] = ["document_image_blurry"]
        expected["quality_expectation"] = "review_required_allowed"
    return {key: value for key, value in expected.items() if value not in (None, "", [])}


def _document_type_from_metadata(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    aliases = {
        "tax_invoice": "invoice",
        "commercial_invoice": "invoice",
        "incoming_inspection": "inspection_report",
        "return_credit": "general_document",
        "internal_transfer": "general_document",
    }
    return aliases.get(normalized, normalized or None)


def compare_expected_actual(expected: dict[str, Any], actual: dict[str, Any], export_json: dict[str, Any] | None = None) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    line_items = actual.get("line_items") if isinstance(actual.get("line_items"), list) else []
    expected_items = expected.get("line_items") if isinstance(expected.get("line_items"), list) else []
    hidden_columns = set(expected.get("hidden_or_cropped_columns") or [])
    visible_columns = set(expected.get("visible_columns") or [])
    no_price_document = bool(expected.get("no_price_document"))

    _compare_document_fields(expected, actual, warnings)
    _detect_no_price_amounts(expected, actual, line_items, failures)
    _detect_hidden_confirmed_values(hidden_columns, line_items, failures)
    _detect_blank_quantity_hallucination(expected_items, line_items, failures)
    _detect_visible_field_mismatch(visible_columns, expected_items, line_items, warnings, failures)
    _detect_summary_or_header_rows(line_items, failures)
    _detect_exchange_rate_as_amount(actual, line_items, failures)
    _detect_vendor_sku_row(line_items, failures)
    _detect_document_expectations(expected, actual, line_items, warnings, failures)
    _detect_review_flags(expected_items, actual, warnings)
    _detect_export_candidate_leak(export_json or {}, failures)

    if no_price_document and actual.get("review_required") and not failures:
        warnings.append(
            {
                "code": "no_price_document_review_required",
                "message": "No-price documents may require review for quantity/crop issues, but export must not be blocked for missing total.",
            }
        )
    status = "FAIL" if failures else ("WARN" if warnings or expected.get("visual_crop") else "PASS")
    return {"status": status, "failures": failures, "warnings": warnings}


def summarize_document(actual: dict[str, Any], export_json: dict[str, Any], provider_metadata: dict[str, Any]) -> dict[str, Any]:
    workflow_metadata = actual.get("workflow_metadata") if isinstance(actual.get("workflow_metadata"), dict) else {}
    vl_summary = workflow_metadata.get("vl_candidate_summary") if isinstance(workflow_metadata.get("vl_candidate_summary"), dict) else {}
    line_items = actual.get("line_items") if isinstance(actual.get("line_items"), list) else []
    return {
        "document_id": actual.get("id") or provider_metadata.get("api_document_id"),
        "processing_status": actual.get("processing_status"),
        "review_required": actual.get("review_required"),
        "extraction_method": actual.get("extraction_method") or provider_metadata.get("api_extraction_method"),
        "provider_chain": actual.get("provider_chain") or provider_metadata.get("api_provider_chain"),
        "fallback_used": vl_summary.get("fallback_used") if vl_summary else provider_metadata.get("ocr_fallback_used"),
        "fallback_reason": vl_summary.get("fallback_reason") or provider_metadata.get("fallback_reason"),
        "promotion_applied": vl_summary.get("promotion_applied"),
        "partial_promotion_applied": vl_summary.get("partial_promotion_applied"),
        "promotion_mode": vl_summary.get("promotion_mode"),
        "gate_decision": vl_summary.get("gate_decision"),
        "gate_reasons": vl_summary.get("gate_reasons") or [],
        "vl_worker_called": _provider_chain_contains(actual, "paddleocr_vl") or bool(vl_summary),
        "document_type": actual.get("document_type"),
        "document_number": actual.get("document_number"),
        "currency": actual.get("currency"),
        "total_amount": actual.get("extracted_amount"),
        "line_item_count": len(line_items),
        "line_items": [_compact_line_item(item) for item in line_items],
        "review_issue_codes": _review_issue_codes(actual),
        "review_candidates_count": _review_candidate_count(actual, export_json),
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "total": len(rows),
        **counts,
        "dangerous_contamination_count": sum(1 for row in rows if row.get("dangerous_contamination")),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Generated VL Primary Regression Report",
        "",
        f"- Sample dir: `{report['sample_dir']}`",
        f"- Output dir: `{report['output_dir']}`",
        f"- Summary: PASS {report['summary'].get('PASS', 0)} / WARN {report['summary'].get('WARN', 0)} / FAIL {report['summary'].get('FAIL', 0)}",
        "",
        "| Status | File | Extraction | Fallback | Promotion | Type | Doc No | Total | Items | Failures | Warnings |",
        "|---|---|---|---|---|---|---|---:|---:|---|---|",
    ]
    for row in report["rows"]:
        summary = row["summary"]
        failures = ", ".join(issue["code"] for issue in row["failures"]) or "-"
        warnings = ", ".join(issue["code"] for issue in row["warnings"][:5]) or "-"
        lines.append(
            "| {status} | {filename} | {method} | {fallback} | {promotion} | {doc_type} | {doc_no} | {total} | {items} | {failures} | {warnings} |".format(
                status=row["status"],
                filename=row["filename"],
                method=summary.get("extraction_method") or "-",
                fallback=summary.get("fallback_used"),
                promotion=summary.get("promotion_mode") or "-",
                doc_type=summary.get("document_type") or "-",
                doc_no=summary.get("document_number") or "-",
                total=summary.get("total_amount") if summary.get("total_amount") is not None else "-",
                items=summary.get("line_item_count"),
                failures=failures,
                warnings=warnings,
            )
        )
    return "\n".join(lines) + "\n"


def _compare_document_fields(expected: dict[str, Any], actual: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    checks = (
        ("document_type", "document_type"),
        ("document_number", "document_number"),
        ("currency", "currency"),
    )
    for expected_key, actual_key in checks:
        expected_value = expected.get(expected_key)
        actual_value = actual.get(actual_key)
        if expected_value in (None, ""):
            continue
        if actual_value in (None, ""):
            warnings.append(
                {
                    "code": f"{actual_key}_missing",
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                }
            )
            continue
        if actual_key == "document_type" and _return_credit_type_matches(expected, actual):
            continue
        if str(expected_value) != str(actual_value):
            warnings.append(
                {
                    "code": f"{actual_key}_mismatch",
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                }
            )
    _compare_header_fields(expected, actual, warnings)
    expected_total = _decimal(expected.get("total_amount"))
    actual_total = _decimal(actual.get("extracted_amount"))
    if expected_total is not None and actual_total is not None and expected_total != actual_total:
        warnings.append(
            {
                "code": "document_total_mismatch",
                "expected_value": str(expected_total),
                "actual_value": str(actual_total),
            }
        )
    for expected_key, actual_keys in (
        ("subtotal", ("subtotal", "supply_amount")),
        ("tax_amount", ("tax_amount", "tax")),
    ):
        expected_amount = _decimal(expected.get(expected_key))
        if expected_amount is None:
            continue
        actual_amount = _actual_document_amount(actual, actual_keys, expected_key)
        if actual_amount is None:
            if expected_key == "tax_amount" and expected_amount == Decimal("0"):
                continue
            warnings.append({"code": f"document_{expected_key}_missing", "expected_value": str(expected_amount)})
        elif actual_amount != expected_amount:
            warnings.append(
                {
                    "code": f"document_{expected_key}_mismatch",
                    "expected_value": str(expected_amount),
                    "actual_value": str(actual_amount),
                }
            )


def _compare_header_fields(expected: dict[str, Any], actual: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    checks = {
        "vendor": ("vendor", "vendor_name", "supplier", "supplier_name"),
        "customer": ("customer", "customer_name", "buyer", "buyer_name"),
        "issue_date": ("issue_date", "document_date", "extracted_date"),
        "due_date": ("due_date", "payment_due_date", "requested_delivery_date", "delivery_due_date"),
        "delivery_date": ("delivery_date", "requested_delivery_date", "delivery_due_date", "due_date", "issue_date", "extracted_date"),
        "valid_until": ("valid_until", "valid_through", "quote_valid_until"),
        "inspection_date": ("inspection_date", "issue_date", "document_date", "extracted_date"),
        "related_document_number": ("related_document_number", "related_document", "related_doc_no", "related_doc_number"),
    }
    for expected_key, aliases in checks.items():
        expected_value = expected.get(expected_key)
        if expected_value in (None, ""):
            continue
        actual_key, actual_value = _first_present(actual, aliases)
        if actual_value in (None, ""):
            warnings.append(
                {
                    "code": f"{expected_key}_missing",
                    "expected_value": expected_value,
                    "actual_field_candidates": list(aliases),
                }
            )
            continue
        if not _header_values_match(expected_key, expected_value, actual_value):
            warnings.append(
                {
                    "code": f"{expected_key}_mismatch",
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                    "actual_field": actual_key,
                }
            )


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, Any]:
    metadata = source.get("workflow_metadata") if isinstance(source.get("workflow_metadata"), dict) else {}
    business_fields = metadata.get("business_fields") if isinstance(metadata.get("business_fields"), dict) else {}
    for key in keys:
        for container_name, container in (("", source), ("workflow_metadata.business_fields.", business_fields)):
            value = container.get(key)
            if value not in (None, "", []):
                return f"{container_name}{key}", value
    return None, None


def _actual_document_amount(actual: dict[str, Any], direct_keys: tuple[str, ...], expected_key: str) -> Decimal | None:
    for key in direct_keys:
        value = _decimal(actual.get(key))
        if value is not None:
            return value
    line_items = actual.get("line_items") if isinstance(actual.get("line_items"), list) else []
    line_field = "supply_amount" if expected_key == "subtotal" else "tax_amount"
    values = [_decimal(item.get(line_field)) for item in line_items if isinstance(item, dict)]
    values = [value for value in values if value is not None]
    if values and len(values) == len(line_items):
        return sum(values, Decimal("0"))
    return None


def _header_values_match(field: str, expected: Any, actual: Any) -> bool:
    if _values_match(expected, actual):
        return True
    if field.endswith("date") or field in {"issue_date", "due_date", "delivery_date", "valid_until", "inspection_date"}:
        return _normalize_date_text(expected) == _normalize_date_text(actual)
    if field == "related_document_number":
        return _normalize_text(expected) in _normalize_text(actual)
    return False


def _return_credit_type_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_type = str(expected.get("document_type") or "").casefold()
    expected_subtype = str(expected.get("document_subtype") or "").casefold()
    expected_profile = str(expected.get("document_profile") or "").casefold()
    expected_profiles = {
        str(value or "").casefold()
        for value in expected.get("document_profiles", [])
        if value not in (None, "")
    }
    expected_policy_values = {expected_type, expected_subtype, expected_profile, *expected_profiles}
    if not expected_policy_values.intersection(
        {"return_credit", "return_credit_note", "return_note", "credit_note", "return_document"}
    ):
        return False
    actual_type = str(actual.get("document_type") or "").casefold()
    actual_category = str(actual.get("category") or "").casefold()
    workflow_metadata = actual.get("workflow_metadata") if isinstance(actual.get("workflow_metadata"), dict) else {}
    taxonomy = workflow_metadata.get("taxonomy") if isinstance(workflow_metadata.get("taxonomy"), dict) else {}
    profile_values = {
        str(actual_category),
        str(taxonomy.get("document_subtype") or "").casefold(),
        str(taxonomy.get("document_profile") or "").casefold(),
        *(str(value or "").casefold() for value in (taxonomy.get("document_profiles") or [])),
    }
    return bool(profile_values.intersection({"return_note", "credit_note", "return_document"})) and actual_type in {
        "general_document",
        "transaction_statement",
        "invoice",
    }


def _detect_no_price_amounts(expected: dict[str, Any], actual: dict[str, Any], line_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    if not expected.get("no_price_document"):
        return
    if any(actual.get(field) not in (None, "", []) for field in ("currency", "subtotal", "tax_amount", "tax", "extracted_amount")):
        failures.append({"code": "no_price_document_amount_blocker", "actual_value": _amount_document_values(actual)})
    for index, item in enumerate(line_items, start=1):
        values = {field: item.get(field) for field in LINE_AMOUNT_FIELDS if item.get(field) not in (None, "", [])}
        if values:
            failures.append({"code": "no_price_line_amount_created", "line_index": index, "actual_value": values})


def _detect_hidden_confirmed_values(hidden_columns: set[str], line_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    field_map = {
        "tax": "tax_amount",
        "amount": "line_total",
        "total": "line_total",
    }
    hidden_fields = {field_map.get(column, column) for column in hidden_columns}
    for index, item in enumerate(line_items, start=1):
        for field in sorted(hidden_fields):
            if field in LINE_AMOUNT_FIELDS and item.get(field) not in (None, "", []):
                failures.append(
                    {
                        "code": "row_amount_hidden_do_not_infer",
                        "line_index": index,
                        "field": field,
                        "actual_value": item.get(field),
                    }
                )


def _detect_blank_quantity_hallucination(expected_items: list[dict[str, Any]], actual_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    for expected in expected_items:
        expected_flags = set(expected.get("expected_review_flags") or [])
        if not expected_flags.intersection({"missing_quantity", "quantity_cell_blank"}):
            continue
        if expected.get("quantity") is not None:
            continue
        actual = _best_matching_item(expected, actual_items)
        if actual and actual.get("quantity") not in (None, "", []):
            failures.append(
                {
                    "code": "blank_quantity_preservation_failed",
                    "item_name": expected.get("item_name"),
                    "actual_value": actual.get("quantity"),
                }
            )


def _detect_visible_field_mismatch(
    visible_columns: set[str],
    expected_items: list[dict[str, Any]],
    actual_items: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    for expected in expected_items:
        actual = _best_matching_item(expected, actual_items)
        if not actual:
            warnings.append({"code": "visible_row_missing", "item_name": expected.get("item_name"), "item_code": expected.get("document_item_code")})
            continue
        for field in sorted(visible_columns):
            expected_value = _expected_field_value(expected, field)
            if expected_value in (None, "", []):
                continue
            actual_field = "specification" if field == "spec" else field
            actual_value = actual.get(actual_field) if actual_field in actual else actual.get(field)
            if actual_value in (None, "", []):
                warnings.append({"code": "visible_field_missing", "field": field, "item_name": expected.get("item_name"), "expected_value": expected_value})
                continue
            if not _values_match(expected_value, actual_value):
                issue = {
                    "code": "visible_field_mismatch",
                    "field": field,
                    "item_name": expected.get("item_name"),
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                }
                if _is_numeric_field(field):
                    failures.append(issue)
                else:
                    warnings.append(issue)


def _detect_summary_or_header_rows(line_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    for index, item in enumerate(line_items, start=1):
        name = str(item.get("item_name") or "").strip().casefold()
        if not name:
            continue
        if any(term in name for term in SUMMARY_ROW_RE):
            failures.append({"code": "summary_total_not_line_item", "line_index": index, "item_name": item.get("item_name")})
        header_hits = sum(1 for term in ("품목명", "규격", "수량", "단가", "공급가액", "세액", "합계", "description", "vendor sku") if term in name)
        if header_hits >= 3:
            failures.append({"code": "header_row_not_line_item", "line_index": index, "item_name": item.get("item_name")})


def _detect_exchange_rate_as_amount(actual: dict[str, Any], line_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    if _decimal(actual.get("extracted_amount")) == Decimal("1370"):
        failures.append({"code": "exchange_rate_not_total", "field": "extracted_amount", "actual_value": actual.get("extracted_amount")})
    for index, item in enumerate(line_items, start=1):
        for field in LINE_AMOUNT_FIELDS + ("quantity",):
            if _decimal(item.get(field)) == Decimal("1370"):
                failures.append({"code": "exchange_rate_not_total", "line_index": index, "field": field, "actual_value": item.get(field)})


def _detect_vendor_sku_row(line_items: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    for index, item in enumerate(line_items, start=1):
        name = str(item.get("item_name") or "").strip().casefold()
        if name in {"vendor sku", "vendor sku column", "sku"} or name.startswith("vendor sku "):
            failures.append({"code": "vendor_sku_not_item_row", "line_index": index, "item_name": item.get("item_name")})


def _detect_document_expectations(
    expected: dict[str, Any],
    actual: dict[str, Any],
    line_items: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    min_count = _int_or_none(expected.get("expected_line_item_min_count"))
    if min_count is not None and len(line_items) < min_count:
        warnings.append(
            {
                "code": "line_item_min_count_not_met",
                "expected_value": min_count,
                "actual_value": len(line_items),
            }
        )

    expected_status = str(expected.get("expected_review_status") or "").strip()
    if expected_status:
        actual_status = str(actual.get("processing_status") or "").strip()
        if actual_status and actual_status != expected_status:
            warnings.append(
                {
                    "code": "review_status_mismatch",
                    "expected_value": expected_status,
                    "actual_value": actual_status,
                }
            )

    required_quality_flags = [str(flag) for flag in (expected.get("expected_quality_flags") or []) if flag]
    if required_quality_flags:
        present = set(_review_issue_codes(actual))
        metadata = actual.get("workflow_metadata") if isinstance(actual.get("workflow_metadata"), dict) else {}
        quality = metadata.get("document_quality") if isinstance(metadata.get("document_quality"), dict) else {}
        present.update(str(reason) for reason in (quality.get("review_reasons") or []) if reason)
        for flag in required_quality_flags:
            if flag not in present:
                warnings.append({"code": "expected_quality_flag_missing", "expected_flag": flag})


def _detect_review_flags(expected_items: list[dict[str, Any]], actual: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    required = {
        flag
        for item in expected_items
        for flag in (item.get("expected_review_flags") or [])
        if flag
    }
    if not required:
        return
    present = set(_review_issue_codes(actual))
    for item in actual.get("line_items") or []:
        for source in (item.get("review_flags"), item.get("validation_warnings")):
            if isinstance(source, list):
                present.update(str(flag) for flag in source if flag)
    aliases = {
        "missing_quantity": {"missing_quantity", "quantity_missing", "vl_candidate_missing_quantity", "vl_candidate_quantity_cell_blank"},
        "quantity_cell_blank": {"quantity_cell_blank", "vl_candidate_quantity_cell_blank", "missing_quantity"},
        "row_amount_hidden_do_not_infer": {"row_amount_hidden_do_not_infer", "amount_column_not_visible", "vl_candidate_missing_line_amount"},
        "amount_column_not_visible": {"amount_column_not_visible", "row_amount_hidden_do_not_infer", "vl_candidate_missing_line_amount"},
        "remaining_quantity_hidden": {"remaining_quantity_hidden", "vl_candidate_remaining_quantity_hidden"},
        "inspection_decision_hidden": {"inspection_decision_hidden", "vl_candidate_inspection_decision_hidden"},
    }
    for flag in required:
        acceptable = aliases.get(flag, {flag})
        if not present.intersection(acceptable):
            warnings.append({"code": "expected_review_flag_missing", "expected_flag": flag})


def _detect_export_candidate_leak(export_json: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    canonical = export_json.get("canonical_export") if isinstance(export_json.get("canonical_export"), dict) else export_json
    line_items = canonical.get("line_items") if isinstance(canonical.get("line_items"), list) else []
    for index, item in enumerate(line_items, start=1):
        if item.get("candidate_only") or item.get("source") in {"vl_candidate", "bbox_table_reconstructor"}:
            failures.append({"code": "review_candidate_leaked_to_export", "line_index": index})


def _best_matching_item(expected: dict[str, Any], actual_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    expected_codes = {
        str(expected.get(key) or "").strip()
        for key in ("document_item_code", "item_code", "internal_item_code", "source_item_code")
        if expected.get(key) not in (None, "", [])
    }
    expected_name = str(expected.get("item_name") or "").strip().casefold()
    for item in actual_items:
        actual_codes = {
            str(item.get(key) or "").strip()
            for key in ("document_item_code", "item_code", "internal_item_code", "source_item_code")
            if item.get(key) not in (None, "", [])
        }
        if expected_codes.intersection(actual_codes):
            return item
        normalized_expected_codes = {normalize_item_text(code) for code in expected_codes if code}
        normalized_actual_codes = {normalize_item_text(code) for code in actual_codes if code}
        if normalized_expected_codes.intersection(normalized_actual_codes):
            return item
    for item in actual_items:
        actual_name = str(item.get("item_name") or "").strip().casefold()
        if expected_name and (expected_name in actual_name or actual_name in expected_name):
            return item
        if expected_name and normalize_item_text(expected_name) == normalize_item_text(actual_name):
            return item
    return None


def _expected_field_value(expected: dict[str, Any], field: str) -> Any:
    if field == "spec":
        return expected.get("spec") or expected.get("specification")
    return expected.get(field)


def _values_match(expected: Any, actual: Any) -> bool:
    expected_decimal = _decimal(expected)
    actual_decimal = _decimal(actual)
    if expected_decimal is not None and actual_decimal is not None:
        return expected_decimal == actual_decimal
    return _normalize_text(expected) == _normalize_text(actual)


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").casefold().split())


def _normalize_date_text(value: Any) -> str:
    text = str(value or "")
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 3:
        year, month, day = numbers[:3]
        if len(year) == 2:
            year = f"20{year}"
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return _normalize_text(value)


def _is_numeric_field(field: str) -> bool:
    return field in {
        "quantity",
        "requested_quantity",
        "ordered_quantity",
        "delivered_quantity",
        "accepted_quantity",
        "rejected_quantity",
        "remaining_quantity",
        "unit_price",
        "supply_amount",
        "tax_amount",
        "line_total",
    }


def _compact_line_item(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "item_name",
        "document_item_code",
        "internal_item_code",
        "specification",
        "quantity",
        "requested_quantity",
        "ordered_quantity",
        "delivered_quantity",
        "accepted_quantity",
        "rejected_quantity",
        "unit",
        "unit_price",
        "supply_amount",
        "tax_amount",
        "line_total",
        "review_flags",
        "validation_warnings",
    )
    return {field: item.get(field) for field in fields if item.get(field) not in (None, "", [])}


def _review_issue_codes(actual: dict[str, Any]) -> list[str]:
    metadata = actual.get("workflow_metadata") if isinstance(actual.get("workflow_metadata"), dict) else {}
    issues = metadata.get("normalized_review_issues") or actual.get("normalized_review_issues") or []
    codes = [str(issue.get("code")) for issue in issues if isinstance(issue, dict) and issue.get("code")]
    summary = metadata.get("vl_candidate_summary") if isinstance(metadata.get("vl_candidate_summary"), dict) else {}
    for source in (summary.get("issue_codes"), summary.get("gate_reasons")):
        if isinstance(source, list):
            codes.extend(str(code) for code in source if code)
    return list(dict.fromkeys(codes))


def _review_candidate_count(actual: dict[str, Any], export_json: dict[str, Any]) -> int:
    metadata = actual.get("workflow_metadata") if isinstance(actual.get("workflow_metadata"), dict) else {}
    count = 0
    for key in ("vl_candidates", "bbox_table_candidates"):
        if isinstance(metadata.get(key), list):
            count += len(metadata[key])
    layout = metadata.get("layout_debug") if isinstance(metadata.get("layout_debug"), dict) else {}
    for key in ("vl_candidates", "bbox_table_candidates"):
        if isinstance(layout.get(key), list):
            count += len(layout[key])
    review_candidates = (export_json.get("canonical_export") or {}).get("review_candidates") if isinstance(export_json.get("canonical_export"), dict) else {}
    if isinstance(review_candidates, dict):
        for key in ("vl_candidates", "bbox_table_candidates"):
            if isinstance(review_candidates.get(key), list):
                count += len(review_candidates[key])
    return count


def _provider_chain_contains(actual: dict[str, Any], needle: str) -> bool:
    chain = actual.get("provider_chain")
    if isinstance(chain, list):
        return any(needle in str(item) for item in chain)
    return needle in str(chain or "")


def _amount_document_values(actual: dict[str, Any]) -> dict[str, Any]:
    return {
        field: actual.get(field)
        for field in ("currency", "subtotal", "tax_amount", "tax", "extracted_amount")
        if actual.get(field) not in (None, "", [])
    }


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", []):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def pdf_text_length(path: Path) -> int:
    try:
        import fitz
    except Exception:
        return 0
    try:
        with fitz.open(path) as doc:
            return sum(len(page.get_text("text") or "") for page in doc)
    except Exception:
        return 0


if __name__ == "__main__":
    main()
