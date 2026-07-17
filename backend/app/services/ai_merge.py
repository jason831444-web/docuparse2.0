from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.document import DocumentType
from app.services.ai_document_understanding import AIDocumentUnderstandingResult
from app.services.parser import ParsedDocument


MANUFACTURING_TYPES = {
    DocumentType.purchase_order,
    DocumentType.quotation,
    DocumentType.transaction_statement,
    DocumentType.delivery_note,
    DocumentType.invoice,
    DocumentType.packing_list,
    DocumentType.inspection_report,
}

MANUFACTURING_CATEGORIES = {
    "purchase_order",
    "quotation",
    "transaction_statement",
    "delivery_note",
    "invoice",
    "packing_list",
    "inspection_report",
    "internal_transfer",
    "return_note",
    "credit_note",
    "pos_daily_settlement",
}

NUMERIC_LINE_FIELDS = {"quantity", "unit_price", "supply_amount", "tax_amount", "line_total"}
CODE_FIELDS = {"item_code", "source_item_code", "internal_item_code"}
WARNING_TEXT = re.compile(r"(확인 필요|검토 필요|비어 있습니다|미확인|후보 확인|미매칭|장부 매칭|신뢰도 낮음)")


@dataclass
class AIMergeResult:
    result: AIDocumentUnderstandingResult
    review_issues: list[dict[str, Any]] = field(default_factory=list)


class AIResultMerger:
    """Conservative merge policy for deterministic parser and AI correction."""

    def merge(self, parsed: ParsedDocument, ai_result: AIDocumentUnderstandingResult) -> AIMergeResult:
        if not self._should_merge(parsed):
            return AIMergeResult(result=ai_result)
        merged = copy.deepcopy(ai_result)
        issues: list[dict[str, Any]] = []

        merged.document_type = parsed.document_type
        merged.category = parsed.category or parsed.document_type.value
        merged.tags = [parsed.document_type.value]
        merged.vendor_name, issues = self._merge_text_field("vendor_name", parsed.vendor_name or parsed.merchant_name, merged.vendor_name, issues)
        merged.customer_name, issues = self._merge_text_field("customer_name", parsed.customer_name, merged.customer_name, issues)
        merged.document_number, issues = self._merge_text_field("document_number", parsed.document_number, merged.document_number, issues)
        merged.issue_date, issues = self._merge_date_field("issue_date", parsed.issue_date or parsed.extracted_date, merged.issue_date, issues)
        merged.due_date, issues = self._merge_date_field("due_date", parsed.due_date, merged.due_date, issues)
        merged.extracted_date = merged.issue_date or parsed.extracted_date or merged.extracted_date
        merged.currency = parsed.currency or merged.currency

        for field_name in ["extracted_amount", "subtotal", "tax"]:
            parser_value = getattr(parsed, field_name)
            ai_value = getattr(merged, field_name)
            value, issues = self._merge_amount_field(field_name, parser_value, ai_value, issues)
            setattr(merged, field_name, value)

        if self._line_items_optional(parsed):
            merged.line_items = list(parsed.line_items or [])
            line_issues = []
        else:
            merged.line_items, line_issues = self._merge_line_items(parsed.line_items, merged.line_items)
        issues.extend(line_issues)
        if issues:
            merged.low_confidence_fields = list(dict.fromkeys(list(merged.low_confidence_fields or []) + [issue["code"] for issue in issues]))
            merged.extraction_notes = list(dict.fromkeys(list(merged.extraction_notes or []) + [issue["message_ko"] for issue in issues]))
            merged.review_required = True
        merged.merge_strategy = "deterministic_parser_first_ai_gap_fill"
        return AIMergeResult(result=merged, review_issues=issues)

    def _should_merge(self, parsed: ParsedDocument) -> bool:
        if parsed.document_type in MANUFACTURING_TYPES:
            return True
        values = {str(parsed.category or "").casefold(), *(str(tag or "").casefold() for tag in parsed.tags or [])}
        return bool(values.intersection(MANUFACTURING_CATEGORIES))

    def _line_items_optional(self, parsed: ParsedDocument) -> bool:
        values = {str(parsed.category or "").casefold(), *(str(tag or "").casefold() for tag in parsed.tags or [])}
        return bool(values.intersection({"pos_daily_settlement", "settlement_summary", "daily_sales_settlement"}))

    def _merge_text_field(self, field: str, parser_value: Any, ai_value: Any, issues: list[dict[str, Any]]):
        parser_text = self._clean_text(parser_value)
        ai_text = self._clean_text(ai_value)
        if parser_text:
            if ai_text and self._normalize_text(parser_text) != self._normalize_text(ai_text):
                issues.append(self._issue(f"{field}_conflict", f"{self._field_label(field)} 값이 parser와 AI 결과에서 다릅니다.", field, expected=parser_text, actual=ai_text))
            return parser_text, issues
        return ai_text, issues

    def _merge_date_field(self, field: str, parser_value: date | None, ai_value: date | None, issues: list[dict[str, Any]]):
        if parser_value:
            if ai_value and parser_value != ai_value:
                issues.append(self._issue(f"{field}_conflict", f"{self._field_label(field)} 값이 parser와 AI 결과에서 다릅니다.", field, expected=str(parser_value), actual=str(ai_value)))
            return parser_value, issues
        return ai_value, issues

    def _merge_amount_field(self, field: str, parser_value: Any, ai_value: Any, issues: list[dict[str, Any]]):
        parser_decimal = self._decimal(parser_value)
        ai_decimal = self._decimal(ai_value)
        if parser_decimal is not None:
            if ai_decimal is not None and parser_decimal != ai_decimal:
                issues.append(self._issue("amount_conflict", f"{self._field_label(field)} 값이 parser와 AI 결과에서 다릅니다.", field, expected=str(parser_decimal), actual=str(ai_decimal)))
            return parser_decimal, issues
        return ai_decimal, issues

    def _merge_line_items(self, parser_items: list[dict], ai_items: list[dict]) -> tuple[list[dict], list[dict[str, Any]]]:
        issues: list[dict[str, Any]] = []
        merged_items = [self._sanitize_line_item(item) for item in parser_items or []]
        used_ai_indexes: set[int] = set()
        for item_index, parser_item in enumerate(merged_items):
            ai_index = self._best_ai_item_index(parser_item, ai_items, used_ai_indexes)
            if ai_index is None:
                continue
            used_ai_indexes.add(ai_index)
            ai_item = self._sanitize_line_item(ai_items[ai_index])
            for field in ["item_name", "item_code", "source_item_code", "specification", "quantity", "unit", "unit_price", "supply_amount", "tax_amount", "line_total"]:
                if parser_item.get(field) in (None, "", []) and ai_item.get(field) not in (None, "", []):
                    parser_item[field] = ai_item[field]
                elif field in NUMERIC_LINE_FIELDS and parser_item.get(field) not in (None, "", []) and ai_item.get(field) not in (None, "", []) and self._decimal(parser_item.get(field)) != self._decimal(ai_item.get(field)):
                    issues.append(self._issue("line_item_amount_conflict", f"{item_index + 1}번째 품목의 {self._field_label(field)} 값이 parser와 AI 결과에서 다릅니다.", f"line_items.{field}", item_index, expected=str(parser_item.get(field)), actual=str(ai_item.get(field))))
        for ai_index, ai_item in enumerate(ai_items or []):
            if ai_index in used_ai_indexes:
                continue
            sanitized = self._sanitize_line_item(ai_item)
            if self._looks_like_duplicate_code_row(sanitized, merged_items):
                issues.append(self._issue("duplicate_sku_as_item_name", "AI 결과에서 문서 품목코드가 별도 품목명처럼 감지되어 병합하지 않았습니다.", "line_items", severity="info"))
            elif not merged_items:
                merged_items.append(sanitized)
            else:
                issues.append(self._issue("ai_line_item_unmatched", "AI가 추가 품목 후보를 제안했지만 기존 품목과 매칭이 불확실합니다.", "line_items"))
        return merged_items, issues

    def _sanitize_line_item(self, item: dict) -> dict:
        sanitized = dict(item or {})
        for field in NUMERIC_LINE_FIELDS:
            sanitized[field] = self._decimal(sanitized.get(field))
        for field in CODE_FIELDS:
            value = self._clean_text(sanitized.get(field))
            sanitized[field] = None if value and WARNING_TEXT.search(value) else value
        for field in ["item_name", "specification", "unit"]:
            sanitized[field] = self._clean_text(sanitized.get(field))
        return sanitized

    def _best_ai_item_index(self, parser_item: dict, ai_items: list[dict], used_ai_indexes: set[int]) -> int | None:
        parser_key = self._item_key(parser_item)
        for index, ai_item in enumerate(ai_items or []):
            if index in used_ai_indexes:
                continue
            if parser_key and parser_key == self._item_key(ai_item):
                return index
        parser_name = self._normalize_text(parser_item.get("item_name"))
        for index, ai_item in enumerate(ai_items or []):
            if index in used_ai_indexes:
                continue
            ai_name = self._normalize_text(ai_item.get("item_name"))
            if parser_name and ai_name and (parser_name in ai_name or ai_name in parser_name):
                return index
        return None

    def _looks_like_duplicate_code_row(self, ai_item: dict, existing_items: list[dict]) -> bool:
        name = self._clean_text(ai_item.get("item_name"))
        if not name:
            return False
        for item in existing_items:
            codes = {self._clean_text(item.get("item_code")), self._clean_text(item.get("source_item_code"))}
            if name in codes:
                return True
        return False

    def _item_key(self, item: dict) -> str:
        code = self._clean_text(item.get("item_code") or item.get("source_item_code"))
        if code:
            return self._normalize_text(code)
        return "|".join([
            self._normalize_text(item.get("item_name")),
            self._normalize_text(item.get("specification")),
            self._normalize_text(item.get("unit")),
        ]).strip("|")

    def _clean_text(self, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = str(value).strip()
        if not text or WARNING_TEXT.search(text):
            return None
        return text

    def _normalize_text(self, value: Any) -> str:
        return re.sub(r"[\s_/-]+", "", str(value or "").lower())

    def _decimal(self, value: Any) -> Decimal | None:
        if value in (None, "", []):
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value).replace(",", "").replace("₩", "").replace("원", "").strip())
        except (InvalidOperation, ValueError):
            return None

    def _issue(self, code: str, message: str, field: str, item_index: int | None = None, severity: str = "warning", expected: str | None = None, actual: str | None = None) -> dict[str, Any]:
        issue: dict[str, Any] = {"code": code, "message_ko": message, "field": field, "severity": severity}
        if item_index is not None:
            issue["item_index"] = item_index
        if expected is not None:
            issue["expected"] = expected
        if actual is not None:
            issue["actual"] = actual
        return issue

    def _field_label(self, field: str) -> str:
        return {
            "vendor_name": "공급업체",
            "customer_name": "고객사",
            "document_number": "문서번호",
            "issue_date": "발행일",
            "due_date": "주요 날짜",
            "extracted_amount": "문서 총액",
            "subtotal": "공급가액",
            "tax": "세액",
            "quantity": "수량",
            "unit_price": "단가",
            "supply_amount": "공급가액",
            "tax_amount": "세액",
            "line_total": "합계금액",
        }.get(field, field)
