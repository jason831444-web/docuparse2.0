from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.document import Document, DocumentType
from app.services.category_taxonomy import normalize_category_value
from app.services.raw_extraction_snapshot import RawExtractionSnapshotService


class SemanticMappingService:
    """Maps reviewed raw extraction data into business-level document semantics."""

    TYPE_LABELS: tuple[tuple[str, str, str], ...] = (
        ("pos_daily_settlement", "general_document", r"POS\s*일일정산|실판매금액|결제합계|카드합계|온라인결제"),
        ("internal_transfer", "general_document", r"자재\s*이동|내부\s*이동|출고창고|입고창고|이동사유"),
        ("incoming_inspection", "inspection_report", r"입고\s*검사|검사판정|검사항목|Lot/Code|Lot\s*No"),
        ("commercial_invoice", "invoice", r"COMMERCIAL\s+INVOICE|Exchange\s*Rate|TOTAL\s*USD|KRW\s*Converted"),
        ("transaction_statement", "transaction_statement", r"거래명세서|Transaction\s*Statement|공급가액|부가세|총합계"),
        ("purchase_order", "purchase_order", r"발주서|Purchase\s*Order|납기일|발주수량"),
        ("quotation", "quotation", r"견적서|Quotation|유효기간|예상\s*합계"),
    )

    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "document_number": ("문서번호", "document no", "doc no", "invoice no"),
        "vendor_name": ("공급자", "공급처", "공급업체", "seller", "vendor"),
        "customer_name": ("공급받는자", "고객사", "buyer", "customer"),
        "issue_date": ("발행일", "작성일", "거래일자", "invoice date", "일자", "요청일"),
        "due_date": ("납기일", "지급기한", "payment due date", "due date"),
        "supply_amount": ("공급가액", "subtotal", "supply amount"),
        "tax_amount": ("부가세", "세액", "v.a.t", "vat", "tax"),
        "document_total": ("총합계", "총 합계", "합계금액", "결제합계", "청구금액", "total", "amount due"),
        "estimated_total": ("예상합계", "예상 합계", "견적합계"),
        "currency": ("통화", "currency"),
        "exchange_rate": ("exchange rate", "환율"),
        "total_usd": ("total usd",),
        "krw_converted": ("krw converted", "원화환산"),
        "from_location": ("출고창고",),
        "to_location": ("입고창고",),
        "request_department": ("요청부서",),
    }

    POS_ALIASES: dict[str, tuple[str, ...]] = {
        "actual_sales_amount": ("실판매금액",),
        "net_sales_amount": ("순판매금액",),
        "taxable_sales_amount": ("과세합계",),
        "supply_amount": ("공급가액",),
        "vat_amount": ("v.a.t", "vat", "부가세"),
        "payment_total": ("결제합계",),
        "cash_total": ("현금합계",),
        "card_total": ("카드합계",),
        "online_payment_total": ("온라인결제",),
        "order_count": ("주문횟수",),
        "in_store_sales_count": ("매장판매",),
        "delivery_sales_count": ("배달판매",),
        "average_unit_price": ("평균단가",),
    }

    TABLE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
        "line_number": ("no", "번호"),
        "item_name": ("품목명", "description", "품명", "item"),
        "item_code": ("품목코드", "규격/코드", "내부코드", "hs/code", "lot/code"),
        "spec": ("규격", "spec"),
        "lot_no": ("lot no", "lot/code"),
        "quantity": ("수량", "입고수량", "발주수량", "요청수량", "qty"),
        "unit": ("단위", "unit"),
        "unit_price": ("단가", "unit price"),
        "supply_amount": ("공급가액",),
        "tax_amount": ("세액",),
        "line_total": ("금액", "합계금액", "amount"),
        "inspection_result": ("판정", "검사판정"),
        "inspection_item": ("검사항목",),
        "note": ("비고", "이동사유"),
    }

    def __init__(self) -> None:
        self.raw_snapshot = RawExtractionSnapshotService()

    def apply_to_document(self, document: Document, *, approval_note: str | None = None) -> dict[str, Any]:
        metadata = dict(document.workflow_metadata or {})
        raw = self.raw_snapshot.build(document, source="confirmed_review")
        mapping = self.map_raw(document, raw)

        metadata["raw_extraction"] = raw
        metadata["confirmed_raw_data"] = {
            **raw,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "approval_note": approval_note,
        }
        metadata["semantic_mapping"] = mapping
        metadata["business_fields"] = {**(metadata.get("business_fields") if isinstance(metadata.get("business_fields"), dict) else {}), **mapping.get("fields", {})}
        metadata["semantic_mapping_version"] = mapping["version"]
        self._apply_document_type(document, mapping)
        document.workflow_metadata = metadata
        return mapping

    def map_raw(self, document: Document, raw: dict[str, Any]) -> dict[str, Any]:
        text = self._semantic_text(document, raw)
        category, document_type = self._classify_type(document, text)
        fields = self._base_fields(document)
        fields.update(self._fields_from_key_values(raw.get("key_values") or [], self.FIELD_ALIASES))
        if category == "pos_daily_settlement":
            fields.update(self._fields_from_key_values(raw.get("key_values") or [], self.POS_ALIASES))
        line_items = self._line_items_from_tables(raw.get("tables") or [])
        mapping_confidence = self._confidence(fields, line_items)
        return {
            "version": "semantic_mapping_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "document_type": document_type,
            "category": category,
            "fields": fields,
            "line_items": line_items,
            "raw_table_count": len(raw.get("tables") or []),
            "raw_key_value_count": len(raw.get("key_values") or []),
            "mapping_confidence": mapping_confidence,
        }

    def _base_fields(self, document: Document) -> dict[str, Any]:
        fields = {
            "document_number": document.document_number,
            "vendor_name": document.vendor_name or document.merchant_name,
            "customer_name": document.customer_name,
            "issue_date": self._string_value(document.issue_date or document.extracted_date),
            "due_date": self._string_value(document.due_date),
            "document_total": self._string_value(document.extracted_amount),
            "supply_amount": self._string_value(document.subtotal),
            "tax_amount": self._string_value(document.tax),
            "currency": document.currency,
        }
        return {key: value for key, value in fields.items() if value not in (None, "")}

    def _fields_from_key_values(self, key_values: list[Any], aliases: dict[str, tuple[str, ...]]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for item in key_values:
            if not isinstance(item, dict):
                continue
            raw_key = str(item.get("key") or "")
            raw_value = item.get("value")
            if raw_value in (None, ""):
                continue
            normalized_key = self._normalize_label(raw_key)
            for target, candidates in aliases.items():
                if target in fields:
                    continue
                if any(self._normalize_label(candidate) in normalized_key for candidate in candidates):
                    fields[target] = self._normalize_business_value(raw_value)
        return fields

    def _line_items_from_tables(self, tables: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for table in tables:
            if not isinstance(table, dict):
                continue
            columns = [str(column) for column in table.get("columns") or []]
            for row in table.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                mapped: dict[str, Any] = {}
                for column in columns or list(row):
                    target = self._table_column_target(column)
                    value = row.get(column)
                    if target and value not in (None, ""):
                        mapped[target] = self._normalize_business_value(value)
                if not mapped:
                    continue
                if mapped.get("item_name") or mapped.get("item_code") or mapped.get("quantity"):
                    items.append(mapped)
        return items

    def _table_column_target(self, column: str) -> str | None:
        normalized = self._normalize_label(column)
        for target, aliases in self.TABLE_COLUMN_ALIASES.items():
            if any(self._normalize_label(alias) == normalized or self._normalize_label(alias) in normalized for alias in aliases):
                return target
        return None

    def _classify_type(self, document: Document, text: str) -> tuple[str, str]:
        current_doc_type = getattr(document.document_type, "value", str(document.document_type or "general_document"))
        current_category = normalize_category_value(document.category) or current_doc_type
        for category, document_type, pattern in self.TYPE_LABELS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return category, document_type
        return current_category or "other", current_doc_type or "general_document"

    def _apply_document_type(self, document: Document, mapping: dict[str, Any]) -> None:
        category = normalize_category_value(str(mapping.get("category") or "")) or document.category
        document.category = category
        raw_type = str(mapping.get("document_type") or "")
        try:
            document.document_type = DocumentType(raw_type)
        except ValueError:
            pass

    def _semantic_text(self, document: Document, raw: dict[str, Any]) -> str:
        values = [document.raw_text or "", document.title or "", str(document.category or "")]
        for item in raw.get("key_values") or []:
            if isinstance(item, dict):
                values.extend([str(item.get("key") or ""), str(item.get("value") or "")])
        for table in raw.get("tables") or []:
            if isinstance(table, dict):
                values.append(str(table.get("table_type") or ""))
                values.extend(str(column) for column in table.get("columns") or [])
        return "\n".join(values)

    def _confidence(self, fields: dict[str, Any], line_items: list[dict[str, Any]]) -> float:
        required = ["document_number", "vendor_name", "customer_name", "issue_date"]
        score = sum(1 for key in required if fields.get(key)) / len(required)
        if fields.get("document_total") or fields.get("payment_total") or fields.get("total_usd"):
            score += 0.2
        if line_items:
            score += 0.2
        return round(min(score, 1.0), 2)

    def _normalize_label(self, value: object) -> str:
        return re.sub(r"[\s:/._·\-]+", "", str(value or "").strip().lower())

    def _normalize_business_value(self, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            numeric = self._decimal_from_text(stripped)
            return str(numeric) if numeric is not None and re.search(r"\d", stripped) and not re.search(r"[A-Za-z가-힣]", stripped.replace(",", "")) else stripped
        return self._string_value(value)

    def _decimal_from_text(self, value: str) -> Decimal | None:
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        if not cleaned or cleaned in {"-", "."}:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _string_value(self, value: object) -> object:
        if isinstance(value, (datetime, Decimal)):
            return str(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()  # type: ignore[no-any-return]
        return value
