from __future__ import annotations

import re
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
        issues = self._issues(parsed, cleaned, manual_visual_check or {}, validation or {})
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
        text: str,
        manual_visual_check: dict[str, Any],
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        issues.extend(self._source_quality_issues(text))
        issues.extend(self._structural_issues(parsed, text))
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

    def _structural_issues(self, parsed: ParsedDocument, text: str) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        doc_type = self._safe_value(parsed.document_type)
        line_items = parsed.line_items or []
        money_document = doc_type in {"purchase_order", "quotation", "transaction_statement", "invoice"}
        no_price_document = doc_type in {"delivery_note", "inspection_report", "general_document", "memo"}

        if money_document and self._text_has_total_label(text) and parsed.extracted_amount is None:
            issues.append(
                {
                    "code": "vl_candidate_missing_document_total",
                    "severity": "warn",
                    "message": "VL output contains total labels but the structured candidate has no document total.",
                }
            )

        if money_document:
            for index, item in enumerate(line_items, start=1):
                has_quantity = item.get("quantity") not in (None, "", [])
                has_any_amount = any(
                    item.get(field) not in (None, "", [])
                    for field in ("unit_price", "supply_amount", "tax_amount", "line_total")
                )
                if has_quantity and not has_any_amount:
                    issues.append(
                        {
                            "code": "vl_candidate_missing_line_amount",
                            "severity": "warn",
                            "line_index": index,
                            "item_name": item.get("item_name"),
                            "message": "A priced VL row has quantity but no unit price, supply amount, tax, or line total.",
                        }
                    )

        for index, item in enumerate(line_items, start=1):
            name = str(item.get("item_name") or "")
            if self._looks_like_table_header(name):
                issues.append(
                    {
                        "code": "vl_candidate_header_row_as_item",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "VL parser promoted a table header-like line as an item row.",
                    }
                )
            if no_price_document and self._looks_like_unparsed_no_price_row(name):
                issues.append(
                    {
                        "code": "vl_candidate_missing_row_cell",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "A no-price VL row appears unparsed; keep it as a review candidate.",
                    }
                )

        lowered = text.casefold()
        if re.search(r"(반품|차감|credit\\s*(?:note|memo)|return)", lowered, flags=re.IGNORECASE) and doc_type not in {
            "transaction_statement",
            "invoice",
        }:
            issues.append(
                {
                    "code": "vl_candidate_return_credit_type_uncertain",
                    "severity": "warn",
                    "actual_value": doc_type,
                    "message": "Return/credit keywords are present but the VL structured document type is not review-safe.",
                }
            )
        if re.search(r"(사업장간|자재\\s*이동|내부\\s*이동|요청수량)", text, flags=re.IGNORECASE) and doc_type not in {
            "general_document",
            "delivery_note",
            "inspection_report",
        }:
            issues.append(
                {
                    "code": "vl_candidate_internal_transfer_type_uncertain",
                    "severity": "warn",
                    "actual_value": doc_type,
                    "message": "Internal-transfer keywords are present but the VL structured document type is not safe to promote.",
                }
            )

        total = self._decimal_value(parsed.extracted_amount)
        row_amounts = [
            value
            for item in line_items
            for value in (self._decimal_value(item.get("supply_amount")), self._decimal_value(item.get("line_total")))
            if value is not None
        ]
        if total is not None and total > 0 and row_amounts and max(row_amounts) > total * Decimal("1.5"):
            issues.append(
                {
                    "code": "vl_candidate_total_row_amount_conflict",
                    "severity": "warn",
                    "actual_value": str(total),
                    "message": "VL candidate row amounts are implausibly larger than the document total.",
                }
            )
        return issues

    def _text_has_total_label(self, text: str) -> bool:
        return bool(re.search(r"(총액|합계\\s*금액|합계금액|total\\s*(?:usd|amount)?|subtotal)", text or "", flags=re.IGNORECASE))

    def _looks_like_table_header(self, value: str) -> bool:
        normalized = " ".join((value or "").split()).casefold()
        if not normalized:
            return False
        header_terms = ("품목명", "규격", "수량", "단가", "공급가액", "세액", "합계", "lot no")
        return normalized.startswith("no ") and sum(1 for term in header_terms if term in normalized) >= 3

    def _looks_like_unparsed_no_price_row(self, value: str) -> bool:
        normalized = " ".join((value or "").split())
        if not normalized:
            return False
        return bool(
            re.match(r"^\\d+\\s+", normalized)
            and re.search(r"(\\bLOT[-\\w]+\\b|\\b[A-Z]{2,}[-\\w]+\\b|\\d+mm|M\\d+|입고수량|합격수량|불량수량)", normalized)
            and len(normalized.split()) >= 6
        )

    def _source_quality_issues(self, text: str) -> list[dict[str, Any]]:
        if not re.search(
            r"(저품질|낮은\s*신뢰|confidence\s*(?:낮|low)|table\s*confidence|distort|왜곡|poor\s*scan)",
            text or "",
            flags=re.IGNORECASE,
        ):
            return []
        return [
            {
                "code": "vl_candidate_untrusted_source_quality",
                "severity": "warn",
                "message": "VL output contains low-confidence or distorted-source signals; require review before ERP export.",
            }
        ]

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

    def _decimal_value(self, value: Any) -> Decimal | None:
        if value in (None, "", []):
            return None
        try:
            return Decimal(str(value).replace(",", ""))
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
