from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

EXPECTED_TERMS_BY_SAMPLE = {
    "08_image_quote_missing_quantity.pdf": [
        "견적서",
        "QT-2026-0808-009",
        "고정",
        "플레이트",
        "스테인리스",
        "473,000",
    ],
    "16_real_commercial_invoice_exchange_rate.pdf": [
        "COMMERCIAL",
        "INV-US-2026-0916-EX",
        "Linear",
        "Cable",
        "PCB",
        "650",
    ],
    "21_photo_fax_po_misaligned_amounts.pdf": [
        "팩스",
        "FAX-PO-2026-0921",
        "베어링",
        "S45C",
        "418,000",
    ],
}

MANUAL_VISUAL_CHECK_TEMPLATES_BY_SAMPLE: dict[str, dict[str, Any]] = {
    "08_image_quote_missing_quantity.pdf": {
        "pdf_opened_and_visually_checked": False,
        "expected_from_pdf": {
            "document_number": "QT-2026-0808-009",
            "total_amount": "473,000",
            "currency": "KRW",
            "row_count": "2",
            "special_cases": "first item quantity is visually blank",
        },
        "required_vl_output_values": ["QT-2026-0808-009", "473,000"],
        "structured_checks": {
            "blank_quantity_rows": [
                {"row_contains": "고정 플레이트", "spec": "120x60x5T", "unit": "EA"},
            ],
        },
        "hallucinations_found": [],
        "dangerous_errors_found": [],
        "notes": "Set pdf_opened_and_visually_checked=true only after opening the rendered PDF/image.",
    },
    "16_real_commercial_invoice_exchange_rate.pdf": {
        "pdf_opened_and_visually_checked": False,
        "expected_from_pdf": {
            "document_number": "INV-US-2026-0916-EX",
            "total_amount": "650.00",
            "currency": "USD",
            "row_count": "3",
            "special_cases": "Exchange rate 1,370 KRW is a note, not a document amount.",
        },
        "required_vl_output_values": ["INV-US-2026-0916-EX", "650.00", "Linear", "Cable", "PCB"],
        "structured_checks": {
            "expected_line_amounts": ["450.00", "110.00", "90.00"],
            "exchange_rate_value": "1,370",
            "expected_row_cells": [
                {"row_contains": "Linear", "cells": ["10", "45.00", "450.00"]},
                {"row_contains": "Cable", "cells": ["50", "2.20", "110.00"]},
                {"row_contains": "PCB", "cells": ["300", "0.30", "90.00"]},
            ],
        },
        "known_input_limitations": [
            "The PDF text layer contains row Amount values, but the rendered image used for VL smoke may omit the far-right Amount column.",
        ],
        "hallucinations_found": [],
        "dangerous_errors_found": [],
        "notes": "Set pdf_opened_and_visually_checked=true only after opening the rendered PDF/image.",
    },
    "21_photo_fax_po_misaligned_amounts.pdf": {
        "pdf_opened_and_visually_checked": False,
        "expected_from_pdf": {
            "document_number": "FAX-PO-2026-0921",
            "total_amount": "418,000",
            "currency": "KRW",
            "row_count": "3",
            "special_cases": "Fax/photo row boundaries are weak; raw evidence missing must remain review candidate only.",
        },
        "required_vl_output_values": ["FAX-PO-2026-0921", "베어링", "S45C"],
        "structured_checks": {
            "expected_document_total": "418,000",
            "expected_row_fragments": [
                {"text": "M8 볼트 / 와셔 SET", "label": "row 3 item name"},
            ],
            "expected_row_cells": [
                {"row_contains": "베어링", "cells": ["20", "8,000", "176,000"]},
                {"row_contains": "S45C", "cells": ["100", "600", "66,000"]},
                {"row_contains": "M8", "cells": ["와셔", "1,000", "SET", "160", "176,000"]},
            ],
        },
        "hallucinations_found": [],
        "dangerous_errors_found": [],
        "notes": "Set pdf_opened_and_visually_checked=true only after opening the rendered PDF/image.",
    },
}

MANUAL_EXPECTED_VALUE_REQUIRED_KEYS = {
    "document_number",
    "invoice_number",
    "related_document_number",
    "vendor",
    "vendor_name",
    "customer",
    "customer_name",
    "currency",
    "subtotal",
    "tax",
    "total",
    "total_amount",
    "document_total",
}


def _configure_runtime_env() -> None:
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_DISABLE_SIGNAL_HANDLER", "1")


def _server_base_url(server_url: str) -> str:
    base = server_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"raw": payload}


def _coerce_payload(payload: Any) -> Any:
    if callable(payload):
        try:
            return payload()
        except TypeError:
            return payload
    return payload


def _strip_html(value: str) -> str:
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value)


def _normalize_line(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return value.strip(" |")


def _is_artifact_path_line(value: str) -> bool:
    lowered = value.lower()
    if not lowered.endswith((".png", ".jpg", ".jpeg", ".pdf")):
        return False
    if lowered.startswith(("imgs/", "./imgs/")):
        return True
    return lowered.startswith(("/tmp/", "/var/tmp/", "/root/", "/app/")) or "/docuparse_e2e_logs/" in lowered


def _walk_strings(value: Any, fragments: list[str]) -> None:
    if isinstance(value, dict):
        for key in ["block_content", "rec_text", "text", "content", "markdown", "html"]:
            content = value.get(key)
            if isinstance(content, str):
                fragments.append(content)
        for nested in value.values():
            _walk_strings(nested, fragments)
    elif isinstance(value, list):
        for nested in value:
            _walk_strings(nested, fragments)
    elif isinstance(value, str):
        fragments.append(value)


def extract_text(output: Any) -> str:
    fragments: list[str] = []
    for item in output or []:
        for attr in ["json", "markdown", "html", "text"]:
            _walk_strings(_coerce_payload(getattr(item, attr, None)), fragments)
        _walk_strings(item, fragments)
    lines: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        for raw in _strip_html(str(fragment)).splitlines():
            line = _normalize_line(raw)
            if not line:
                continue
            if _is_artifact_path_line(line):
                continue
            key = line.casefold()
            if key not in seen:
                seen.add(key)
                lines.append(line)
    return "\n".join(lines)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return repr(value)[:300]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(key): _json_safe(nested, depth=depth + 1) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested, depth=depth + 1) for nested in value[:200]]
    return repr(value)[:1000]


def summarize_output(output: Any, *, max_items: int = 80) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, item in enumerate(output or []):
        if len(summary) >= max_items:
            break
        entry: dict[str, Any] = {"index": index, "type": type(item).__name__}
        for attr in ["json", "markdown", "html", "text"]:
            value = _coerce_payload(getattr(item, attr, None))
            if value is None:
                continue
            if isinstance(value, str):
                entry[attr] = value[:1000]
            else:
                entry[attr] = _json_safe(value)
        summary.append(entry)
    return summary


def validate_output_text(text: str, expected_terms: list[str] | None = None) -> dict[str, Any]:
    expected_terms = expected_terms or []
    stripped = text.strip()
    matched_terms = [term for term in expected_terms if term in stripped]
    repeated_char = bool(re.search(r"(.)\1{30,}", stripped))
    replacement_ratio = stripped.count("\ufffd") / max(len(stripped), 1)
    prompt_echo_only = stripped.lower() in {"ocr:", "read the document", "read the visible text"}
    ok = bool(stripped) and len(stripped) >= 20 and not repeated_char and replacement_ratio < 0.08 and not prompt_echo_only
    if expected_terms:
        ok = ok and bool(matched_terms)
    if not stripped:
        status = "output_empty"
    elif repeated_char or replacement_ratio >= 0.08:
        status = "degenerate_generation"
    elif expected_terms and not matched_terms:
        status = "document_terms_missing"
    elif prompt_echo_only:
        status = "prompt_echo_only"
    elif len(stripped) < 20:
        status = "output_too_short"
    else:
        status = "official_gguf_smoke_success" if ok else "candidate_text_generated"
    return {"ok": ok, "status": status, "text_length": len(stripped), "matched_terms": matched_terms}


def classify_smoke_exception(exc: Exception) -> str:
    message = repr(exc).lower()
    if isinstance(exc, ImportError) and "paddleocrvl" in message:
        return "paddleocr_vl_runtime_missing_dependency"
    if "gguf_model_missing" in message:
        return "gguf_model_missing"
    if "sample_missing" in message:
        return "sample_missing"
    if "urlopen" in message or "connection" in message:
        return "llama_server_unreachable"
    if "timeout" in message:
        return "official_runtime_timeout"
    if "missing" in message:
        return "model_or_sample_missing"
    return "paddleocr_vl_gguf_backend_error"


def _line_containing(text: str, needle: str) -> str | None:
    needle_folded = needle.casefold()
    for line in text.splitlines():
        if needle_folded in line.casefold():
            return line
    return None


def _structured_manual_issues(text: str, manual_visual_check: dict[str, Any]) -> list[dict[str, Any]]:
    checks = manual_visual_check.get("structured_checks") or {}
    issues: list[dict[str, Any]] = []

    for value in checks.get("expected_line_amounts") or []:
        value = str(value).strip()
        if value and value not in text:
            issues.append(
                {
                    "code": "vl_candidate_missing_line_amount",
                    "severity": "warn",
                    "expected_value": value,
                    "message": "A visible line amount from the source PDF is missing in the VL output.",
                }
            )

    document_total = str(checks.get("expected_document_total") or "").strip()
    if document_total and document_total not in text:
        issues.append(
            {
                "code": "vl_candidate_missing_document_total",
                "severity": "warn",
                "expected_value": document_total,
                "message": "The source PDF total is missing in the VL output.",
            }
        )

    row_anchors = checks.get("expected_row_anchors") or checks.get("required_row_anchors") or []
    for anchor in row_anchors:
        if isinstance(anchor, dict):
            anchor_text = str(anchor.get("text") or anchor.get("contains") or "").strip()
            label = str(anchor.get("label") or anchor_text).strip()
        else:
            anchor_text = str(anchor or "").strip()
            label = anchor_text
        if anchor_text and anchor_text not in text:
            issues.append(
                {
                    "code": "vl_candidate_missing_row_anchor",
                    "severity": "warn",
                    "expected_value": anchor_text,
                    "label": label,
                    "message": "A visually verified row/item anchor from the source PDF is missing in the VL output.",
                }
            )

    for fragment in checks.get("expected_row_fragments") or []:
        if isinstance(fragment, dict):
            fragment_text = str(fragment.get("text") or fragment.get("contains") or "").strip()
            label = str(fragment.get("label") or fragment_text).strip()
        else:
            fragment_text = str(fragment or "").strip()
            label = fragment_text
        if fragment_text and fragment_text not in text:
            issues.append(
                {
                    "code": "vl_candidate_missing_row_fragment",
                    "severity": "warn",
                    "expected_value": fragment_text,
                    "label": label,
                    "message": "A visually verified row fragment from the source PDF is missing or degraded in the VL output.",
                }
            )

    for row_check in checks.get("expected_row_cells") or []:
        if not isinstance(row_check, dict):
            continue
        row_contains = str(row_check.get("row_contains") or "").strip()
        expected_cells = [str(cell).strip() for cell in row_check.get("cells") or [] if str(cell).strip()]
        if not row_contains or not expected_cells:
            continue
        line = _line_containing(text, row_contains)
        if line is None:
            issues.append(
                {
                    "code": "vl_candidate_missing_row_anchor",
                    "severity": "warn",
                    "expected_value": row_contains,
                    "message": "A visually verified row anchor from the source PDF is missing in the VL output.",
                }
            )
            continue
        for cell in expected_cells:
            if cell not in line:
                issues.append(
                    {
                        "code": "vl_candidate_missing_row_cell",
                        "severity": "warn",
                        "row_contains": row_contains,
                        "expected_value": cell,
                        "line": line,
                        "message": "A visually verified row cell from the source PDF is missing from its VL output row.",
                    }
                )

    for guard in checks.get("blank_quantity_rows") or []:
        row_contains = str(guard.get("row_contains") or "").strip()
        unit = str(guard.get("unit") or "").strip()
        spec = str(guard.get("spec") or "").strip()
        if not row_contains or not unit:
            continue
        line = _line_containing(text, row_contains)
        if not line:
            continue
        if spec:
            pattern = rf"{re.escape(spec)}\s+(?P<quantity>[0-9][0-9,]*(?:\.[0-9]+)?)\s+{re.escape(unit)}\b"
        else:
            pattern = rf"{re.escape(row_contains)}.*?(?P<quantity>[0-9][0-9,]*(?:\.[0-9]+)?)\s+{re.escape(unit)}\b"
        match = re.search(pattern, line)
        if match:
            issues.append(
                {
                    "code": "vl_candidate_hallucinated_blank_quantity",
                    "severity": "fail",
                    "row_contains": row_contains,
                    "quantity": match.group("quantity"),
                    "line": line,
                    "message": "A visually blank quantity cell appears filled in the VL output.",
                }
            )

    exchange_rate_value = str(checks.get("exchange_rate_value") or "").strip()
    if exchange_rate_value:
        total_label_pattern = re.compile(r"(?i)(total|subtotal|amount|합계|총액|공급가액|세액)")
        for line in text.splitlines():
            if exchange_rate_value in line and total_label_pattern.search(line):
                if not re.search(r"(?i)(exchange\s*rate|환율|참고|기준)", line):
                    issues.append(
                        {
                            "code": "vl_candidate_exchange_rate_as_amount",
                            "severity": "fail",
                            "value": exchange_rate_value,
                            "line": line,
                            "message": "An exchange-rate value appears in an amount/total context.",
                        }
                    )

    return issues


def _expected_terms_for_sample(sample: Path) -> list[str]:
    return EXPECTED_TERMS_BY_SAMPLE.get(sample.name, [])


def manual_visual_check_template_for_sample(sample: Path) -> dict[str, Any]:
    template = MANUAL_VISUAL_CHECK_TEMPLATES_BY_SAMPLE.get(
        sample.name,
        {
            "pdf_opened_and_visually_checked": False,
            "expected_from_pdf": {},
            "required_vl_output_values": _expected_terms_for_sample(sample),
            "structured_checks": {},
            "hallucinations_found": [],
            "dangerous_errors_found": [],
            "notes": "Fill this template after opening the rendered PDF/image. Do not mark true from filename or fixture memory.",
        },
    )
    return json.loads(json.dumps(template, ensure_ascii=False))


def write_manual_visual_check_template(sample: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manual_visual_check_template_for_sample(sample), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_manual_visual_check(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manual_visual_check_file_must_contain_json_object")
    data.setdefault("pdf_opened_and_visually_checked", False)
    data.setdefault("hallucinations_found", [])
    data.setdefault("dangerous_errors_found", [])
    return data


def apply_cli_runtime_overrides(
    *,
    model_dir: Path | None = None,
    model_file: str | None = None,
    mmproj_file: str | None = None,
    server_url: str | None = None,
    concurrency: int | None = None,
) -> dict[str, str]:
    """Apply smoke-only runtime overrides before Settings is loaded.

    The production service reads these values from env/compose. The smoke script
    also accepts explicit CLI values so host venv, backend container, and server
    diagnostics can be reproduced without editing `.env`.
    """

    overrides: dict[str, str] = {}
    if model_dir is not None:
        overrides["PADDLEOCR_VL_GGUF_MODEL_DIR"] = str(model_dir)
    if model_file:
        overrides["PADDLEOCR_VL_GGUF_MODEL_FILE"] = model_file
    if mmproj_file:
        overrides["PADDLEOCR_VL_GGUF_MMPROJ_FILE"] = mmproj_file
    if server_url:
        overrides["PADDLEOCR_VL_GGUF_SERVER_URL"] = server_url
    if concurrency is not None:
        overrides["PADDLEOCR_VL_GGUF_CONCURRENCY"] = str(concurrency)
    for key, value in overrides.items():
        os.environ[key] = value
    if overrides:
        from app.core.config import get_settings

        get_settings.cache_clear()
    return overrides


def _evaluate_manual_visual_check(text: str, manual_visual_check: dict[str, Any] | None) -> dict[str, Any] | None:
    if not manual_visual_check:
        return None
    expected = manual_visual_check.get("expected_from_pdf") or {}
    expected_values = {
        key: value
        for key, value in expected.items()
        if isinstance(value, str) and value.strip() and key not in {"notes"}
    }
    matched_expected_values = {
        key: value for key, value in expected_values.items() if value in text
    }
    dangerous_errors = list(manual_visual_check.get("dangerous_errors_found") or [])
    hallucinations = list(manual_visual_check.get("hallucinations_found") or [])
    required_values = [
        str(value)
        for value in manual_visual_check.get("required_vl_output_values") or []
        if str(value).strip()
    ]
    matched_required_values = [value for value in required_values if value in text]
    missing_required_values = [value for value in required_values if value not in text]
    issues = _structured_manual_issues(text, manual_visual_check)
    for key, value in expected_values.items():
        if key in MANUAL_EXPECTED_VALUE_REQUIRED_KEYS and key not in matched_expected_values:
            issues.append(
                {
                    "code": "vl_candidate_missing_expected_pdf_value",
                    "severity": "warn",
                    "field": key,
                    "expected_value": value,
                    "message": "A manually verified source PDF value is missing in the VL output.",
                }
            )
    for value in missing_required_values:
        issues.append(
            {
                "code": "vl_candidate_missing_required_value",
                "severity": "warn",
                "expected_value": value,
                "message": "A required manually verified value is missing in the VL output.",
            }
        )
    for error in dangerous_errors:
        issues.append(
            {
                "code": "vl_candidate_dangerous_manual_error",
                "severity": "fail",
                "message": str(error),
            }
        )
    for hallucination in hallucinations:
        issues.append(
            {
                "code": "vl_candidate_manual_hallucination",
                "severity": "fail",
                "message": str(hallucination),
            }
        )
    for limitation in manual_visual_check.get("known_input_limitations") or []:
        issues.append(
            {
                "code": "vl_candidate_known_input_limitation",
                "severity": "warn",
                "message": str(limitation),
            }
        )
    severity = "pass"
    if any(issue.get("severity") == "fail" for issue in issues):
        severity = "fail"
    elif any(issue.get("severity") == "warn" for issue in issues):
        severity = "warn"
    ok = (
        bool(manual_visual_check.get("pdf_opened_and_visually_checked"))
        and not dangerous_errors
        and not hallucinations
    )
    return {
        "ok": ok,
        "matched_expected_values": matched_expected_values,
        "missing_expected_values": {
            key: value for key, value in expected_values.items() if key not in matched_expected_values
        },
        "matched_required_values": matched_required_values,
        "missing_required_values": missing_required_values,
        "issues": issues,
        "issue_codes": [str(issue.get("code")) for issue in issues if issue.get("code")],
        "dangerous_error_count": len(dangerous_errors),
        "hallucination_count": len(hallucinations),
        "severity": severity,
    }


def decide_provider_available_candidate(
    validation: dict[str, Any],
    manual_visual_check: dict[str, Any] | None,
    manual_validation: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not validation.get("ok"):
        return False, str(validation.get("status") or "output_validation_failed")
    if not manual_visual_check:
        return False, "manual_visual_check_missing"
    if not manual_validation or not manual_validation.get("ok"):
        return False, "manual_visual_check_failed"
    severity = manual_validation.get("severity")
    if severity != "pass":
        return False, f"manual_visual_check_{severity or 'not_pass'}"
    return True, "manual_visual_check_passed"


def recommend_candidate_handling(
    *,
    provider_available_candidate: bool,
    manual_validation: dict[str, Any] | None,
) -> str:
    manual_validation = manual_validation or {}
    severity = manual_validation.get("severity")
    issue_codes = {str(code) for code in manual_validation.get("issue_codes") or [] if code}
    if severity == "fail":
        return "reject_vl_candidate"
    if "vl_candidate_known_input_limitation" in issue_codes:
        return "use_parser_primary_vl_auxiliary"
    if issue_codes:
        return "review_candidate_only"
    if provider_available_candidate and severity == "pass":
        return "candidate_evidence_only"
    return "candidate_only"


def build_docuparse_vl_candidate_metadata(report: dict[str, Any]) -> dict[str, Any]:
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    manual_validation = (
        report.get("manual_visual_check_validation")
        if isinstance(report.get("manual_visual_check_validation"), dict)
        else {}
    )
    issue_details = [
        issue for issue in manual_validation.get("issues", []) if isinstance(issue, dict)
    ][:20]
    raw_issue_codes = [
        str(code)
        for code in manual_validation.get("issue_codes", [])
        if code not in (None, "")
    ]
    issue_codes = list(dict.fromkeys(raw_issue_codes))
    severity = manual_validation.get("severity")
    recommended_handling = recommend_candidate_handling(
        provider_available_candidate=bool(report.get("provider_available_candidate")),
        manual_validation=manual_validation,
    )
    candidate = {
        "source": "paddleocr_vl_gguf_smoke",
        "provider": "paddleocr_vl_1_6_gguf",
        "candidate_only": True,
        "parser_integrated": False,
        "recommended_handling": recommended_handling,
        "provider_available_candidate": bool(report.get("provider_available_candidate")),
        "provider_available_decision_reason": report.get("provider_available_decision_reason"),
        "validation_severity": severity,
        "issue_codes": issue_codes,
        "issue_details": issue_details,
        "review_flags": issue_codes,
        "matched_terms": validation.get("matched_terms") or [],
        "text_preview": str(report.get("text_preview") or "")[:1200],
        "inference_time_ms": report.get("elapsed_ms"),
    }
    summary = {
        "candidate_count": 1 if report.get("text_preview") else 0,
        "warning_count": 1 if severity == "warn" else 0,
        "failure_count": 1 if severity == "fail" else 0,
        "issue_codes": issue_codes,
        "parser_integrated": False,
        "provider": "paddleocr_vl_1_6_gguf",
        "provider_available_candidate": bool(report.get("provider_available_candidate")),
        "recommended_handling": recommended_handling,
    }
    return {
        "vl_candidates": [candidate] if summary["candidate_count"] else [],
        "vl_candidate_summary": summary,
    }


def _render_first_page(sample: Path, output_dir: Path, *, scale: float = 2.0) -> dict[str, Any]:
    import fitz

    output_dir.mkdir(parents=True, exist_ok=True)
    png = output_dir / "sample_page_1.png"
    doc = fitz.open(str(sample))
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pix.save(str(png))
    return {
        "page_count": doc.page_count,
        "image_path": str(png),
        "width": pix.width,
        "height": pix.height,
        "scale": scale,
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paddleocr_vl_gguf_smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# PaddleOCR-VL GGUF Smoke Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Classification: `{report.get('classification')}`",
        f"- Provider candidate: `{report.get('provider_available_candidate')}`",
        f"- Provider candidate reason: `{report.get('provider_available_decision_reason')}`",
        f"- Server URL: `{report.get('server_url')}`",
        f"- Model: `{report.get('model')}`",
        f"- Sample: `{report.get('sample')}`",
        f"- Elapsed ms: `{report.get('elapsed_ms')}`",
        f"- Error: `{report.get('error')}`",
        f"- Matched terms: `{', '.join((report.get('validation') or {}).get('matched_terms') or [])}`",
        "",
        "## Manual Validation",
        "",
        "```json",
        json.dumps(report.get("manual_visual_check_validation") or {}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## DocuParse Candidate Metadata",
        "",
        "```json",
        json.dumps(report.get("docuparse_candidate_metadata") or {}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Manual Visual Check",
        "",
        "```json",
        json.dumps(report.get("manual_visual_check") or {}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Output Preview",
        "",
        "```text",
        str(report.get("text_preview") or "")[:3000],
        "```",
    ]
    (output_dir / "paddleocr_vl_gguf_smoke_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_smoke(
    sample: Path,
    output_dir: Path,
    *,
    manual_visual_check: dict[str, Any] | None = None,
    expected_terms: list[str] | None = None,
    render_scale: float = 2.0,
) -> dict[str, Any]:
    _configure_runtime_env()
    from app.core.config import get_settings

    settings = get_settings()
    started = time.perf_counter()
    model_dir = settings.paddleocr_vl_gguf_model_dir
    model_file = model_dir / settings.paddleocr_vl_gguf_model_file
    mmproj_file = model_dir / settings.paddleocr_vl_gguf_mmproj_file
    server_base = _server_base_url(settings.paddleocr_vl_gguf_server_url)
    report: dict[str, Any] = {
        "ok": False,
        "classification": None,
        "sample": str(sample),
        "server_url": settings.paddleocr_vl_gguf_server_url,
        "model": settings.paddleocr_vl_gguf_model_file,
        "mmproj": settings.paddleocr_vl_gguf_mmproj_file,
        "model_dir": str(model_dir),
        "python": sys.version,
        "platform": platform.platform(),
        "runtime": "paddleocr.PaddleOCRVL+llama-cpp-server",
        "error": None,
        "provider_available_candidate": False,
        "manual_visual_check": manual_visual_check
        or {
            "sample": str(sample),
            "pdf_opened_and_visually_checked": False,
            "notes": "No manual visual check file was provided.",
        },
    }
    try:
        if not sample.exists():
            raise FileNotFoundError(f"sample_missing: {sample}")
        if not model_file.exists() or not mmproj_file.exists():
            raise FileNotFoundError("gguf_model_missing")
        report["llama_server_health"] = _get_json(f"{server_base}/health")
        report["llama_server_models"] = _get_json(f"{server_base}/v1/models")
        render = _render_first_page(sample, output_dir, scale=render_scale)
        report["render"] = render
        from paddleocr import PaddleOCRVL

        pipeline_started = time.perf_counter()
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            device="cpu",
            vl_rec_backend="llama-cpp-server",
            vl_rec_server_url=settings.paddleocr_vl_gguf_server_url,
            vl_rec_api_model_name=settings.paddleocr_vl_gguf_model_file,
            vl_rec_max_concurrency=settings.paddleocr_vl_gguf_concurrency,
            use_queues=False,
        )
        report["pipeline_created_ms"] = int((time.perf_counter() - pipeline_started) * 1000)
        predict_started = time.perf_counter()
        output = pipeline.predict(render["image_path"])
        report["predict_ms"] = int((time.perf_counter() - predict_started) * 1000)
        report["output_blocks_preview"] = summarize_output(output)
        text = extract_text(output)
        terms = expected_terms if expected_terms is not None else _expected_terms_for_sample(sample)
        validation = validate_output_text(text, terms)
        manual_validation = _evaluate_manual_visual_check(text, manual_visual_check)
        provider_candidate, provider_candidate_reason = decide_provider_available_candidate(
            validation,
            manual_visual_check,
            manual_validation,
        )
        report.update(
            {
                "ok": bool(validation["ok"]),
                "classification": validation["status"],
                "validation": validation,
                "manual_visual_check_validation": manual_validation,
                "output_type": str(type(output)),
                "text_preview": text[:5000],
                "provider_available_candidate": provider_candidate,
                "provider_available_decision_reason": provider_candidate_reason,
            }
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc!r}"
        report["classification"] = classify_smoke_exception(exc)
    finally:
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        report["docuparse_candidate_metadata"] = build_docuparse_vl_candidate_metadata(report)
        _write_report(output_dir, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test PaddleOCR-VL-1.6 GGUF through llama-cpp-server.")
    parser.add_argument(
        "--sample",
        type=Path,
        default=Path("samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke"))
    parser.add_argument(
        "--manual-visual-check-file",
        type=Path,
        help="JSON object recording PDF values verified by looking at the rendered source PDF.",
    )
    parser.add_argument(
        "--write-manual-visual-check-template",
        type=Path,
        help="Write a sample-specific manual visual check template and exit without running VL inference.",
    )
    parser.add_argument(
        "--expected-term",
        action="append",
        default=None,
        help="Expected text fragment for output validation. Repeat to override sample defaults.",
    )
    parser.add_argument(
        "--render-scale",
        type=float,
        default=2.0,
        help="PyMuPDF render scale for the first page image passed to PaddleOCR-VL.",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        help="Override PADDLEOCR_VL_GGUF_MODEL_DIR for this smoke run.",
    )
    parser.add_argument(
        "--model-file",
        help="Override PADDLEOCR_VL_GGUF_MODEL_FILE for this smoke run.",
    )
    parser.add_argument(
        "--mmproj-file",
        help="Override PADDLEOCR_VL_GGUF_MMPROJ_FILE for this smoke run.",
    )
    parser.add_argument(
        "--server-url",
        help="Override PADDLEOCR_VL_GGUF_SERVER_URL for this smoke run.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override PADDLEOCR_VL_GGUF_CONCURRENCY for this smoke run.",
    )
    args = parser.parse_args()
    if args.write_manual_visual_check_template:
        write_manual_visual_check_template(args.sample, args.write_manual_visual_check_template)
        print(f"Wrote {args.write_manual_visual_check_template}")
        return
    apply_cli_runtime_overrides(
        model_dir=args.model_dir,
        model_file=args.model_file,
        mmproj_file=args.mmproj_file,
        server_url=args.server_url,
        concurrency=args.concurrency,
    )
    report = run_smoke(
        args.sample,
        args.output_dir,
        manual_visual_check=_load_manual_visual_check(args.manual_visual_check_file),
        expected_terms=args.expected_term,
        render_scale=args.render_scale,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
