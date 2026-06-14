from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.services.parser import DocumentParser, ParsedDocument


class VLCandidateParser:
    """Parse VL text into review-only structured candidates.

    This adapter intentionally does not promote VL output into confirmed
    document fields. It only makes the candidate inspectable by review/export
    layers so the normal parser and validation guardrails remain authoritative.
    """

    provider = "paddleocr_vl_1_6_gguf"

    def __init__(self, parser: DocumentParser | None = None) -> None:
        self.parser = parser or DocumentParser()

    def parse_text(
        self,
        text: str,
        *,
        filename: str = "",
        manual_visual_check: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cleaned = self._clean_text(text)
        if not cleaned:
            return None
        parsed = self.parser.parse(cleaned, filename)
        issues = self._issues(parsed, manual_visual_check or {}, validation or {})
        return {
            "source": "vl_candidate_parser",
            "provider": self.provider,
            "candidate_only": True,
            "parser_integrated": False,
            "parser_evaluated": True,
            "confirmed_promotion": False,
            "document": self._compact_document(parsed),
            "line_items": [self._compact_line_item(item) for item in parsed.line_items[:25]],
            "line_item_count": len(parsed.line_items),
            "issue_codes": list(dict.fromkeys(issue["code"] for issue in issues if issue.get("code"))),
            "issues": issues,
            "review_flags": list(dict.fromkeys(issue["code"] for issue in issues if issue.get("code"))),
        }

    def _clean_text(self, text: str) -> str:
        lines = []
        for raw in (text or "").splitlines():
            line = " ".join(raw.strip().split())
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _compact_document(self, parsed: ParsedDocument) -> dict[str, Any]:
        return {
            "document_type": self._safe_value(parsed.document_type),
            "document_number": parsed.document_number,
            "vendor_name": parsed.vendor_name or parsed.merchant_name,
            "customer_name": parsed.customer_name,
            "issue_date": self._safe_value(parsed.issue_date or parsed.extracted_date),
            "due_date": self._safe_value(parsed.due_date),
            "currency": parsed.currency,
            "subtotal": self._safe_value(parsed.subtotal),
            "tax": self._safe_value(parsed.tax),
            "total": self._safe_value(parsed.extracted_amount),
            "business_fields": self._safe_value(parsed.business_fields),
        }

    def _compact_line_item(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "item_name",
            "item_code",
            "document_item_code",
            "internal_item_code",
            "source_item_code",
            "specification",
            "quantity",
            "ordered_quantity",
            "requested_quantity",
            "received_quantity",
            "delivered_quantity",
            "remaining_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "unit",
            "unit_price",
            "supply_amount",
            "tax_amount",
            "line_total",
            "validation_warnings",
            "review_flags",
        )
        return {
            field: self._safe_value(item.get(field))
            for field in fields
            if item.get(field) not in (None, "", [])
        }

    def _issues(
        self,
        parsed: ParsedDocument,
        manual_visual_check: dict[str, Any],
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        issues.extend(self._line_item_warning_issues(parsed))
        expected = manual_visual_check.get("expected_from_pdf") if isinstance(manual_visual_check, dict) else {}
        if isinstance(expected, dict):
            self._append_expected_document_issues(issues, parsed, expected)
        validation_status = validation.get("status") if isinstance(validation, dict) else None
        if validation_status in {"warn", "fail"}:
            issues.append(
                {
                    "code": "vl_candidate_requires_review",
                    "severity": "warn" if validation_status == "warn" else "fail",
                    "message": "VL output validation did not fully pass; keep this as a review candidate.",
                }
            )
        return issues

    def _line_item_warning_issues(self, parsed: ParsedDocument) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for index, item in enumerate(parsed.line_items or [], start=1):
            for warning in item.get("validation_warnings") or []:
                issues.append(
                    {
                        "code": f"vl_candidate_{warning}",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "Parser found a row-level warning while structuring VL output.",
                    }
                )
        return issues

    def _append_expected_document_issues(
        self,
        issues: list[dict[str, Any]],
        parsed: ParsedDocument,
        expected: dict[str, Any],
    ) -> None:
        expected_number = str(expected.get("document_number") or "").strip()
        if expected_number and parsed.document_number and parsed.document_number != expected_number:
            issues.append(
                {
                    "code": "vl_candidate_document_number_mismatch",
                    "severity": "fail",
                    "expected_value": expected_number,
                    "actual_value": parsed.document_number,
                }
            )
        expected_total = self._decimal_text(expected.get("total_amount"))
        actual_total = self._decimal_text(parsed.extracted_amount)
        if expected_total and actual_total and expected_total != actual_total:
            issues.append(
                {
                    "code": "vl_candidate_total_mismatch",
                    "severity": "warn",
                    "expected_value": expected_total,
                    "actual_value": actual_total,
                }
            )
        expected_rows = self._int_value(expected.get("row_count"))
        if expected_rows is not None and parsed.line_items and len(parsed.line_items) != expected_rows:
            issues.append(
                {
                    "code": "vl_candidate_row_count_mismatch",
                    "severity": "warn",
                    "expected_value": expected_rows,
                    "actual_value": len(parsed.line_items),
                }
            )

    def _decimal_text(self, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        try:
            return str(Decimal(str(value).replace(",", "")))
        except Exception:
            return None

    def _int_value(self, value: Any) -> int | None:
        try:
            return int(str(value).strip())
        except Exception:
            return None

    def _safe_value(self, value: Any) -> Any:
        if isinstance(value, (Decimal, date)):
            return str(value)
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._safe_value(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [self._safe_value(inner) for inner in value]
        return value
