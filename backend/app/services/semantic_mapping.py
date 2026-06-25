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
        ("tax_invoice", "invoice", r"세금\s*계산서|전자\s*세금\s*계산서|Tax\s*Invoice|승인번호"),
        ("commercial_invoice", "invoice", r"COMMERCIAL\s+INVOICE|Exchange\s*Rate|TOTAL\s*USD|KRW\s*Converted"),
        ("credit_note", "general_document", r"반품|크레[딧뒷]|Credit\s*(?:Memo|Note)|원문서|차감\s*합계"),
        ("transaction_statement", "transaction_statement", r"거래명세서|Transaction\s*Statement|공급가액|부가세|총합계"),
        ("purchase_order", "purchase_order", r"발주서|Purchase\s*Order|납기일|발주수량"),
        ("quotation", "quotation", r"견적서|Quotation|유효기간|예상\s*합계"),
    )

    FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "document_number": ("문서번호", "document no", "doc no", "invoice no"),
        "sample_id": ("샘플번호", "sample no", "sample id"),
        "reference_number": ("참조번호", "관련문서번호", "관련 문서번호", "원문서", "원 문서", "reference no", "reference number"),
        "vendor_name": ("공급자", "공급처", "공급업체", "seller", "vendor"),
        "customer_name": ("공급받는자", "고객사", "buyer", "customer"),
        "issue_date": ("발행일", "작성일", "거래일자", "invoice date", "견적일", "일자", "요청일"),
        "due_date": ("납기일", "지급기한", "payment due date", "due date"),
        "supply_amount": ("공급가액", "subtotal", "supply amount"),
        "tax_amount": ("부가세", "세액", "v.a.t", "vat", "tax"),
        "document_total": ("총합계", "총 합계", "합계금액", "결제합계", "청구금액", "결제금액", "합계", "total", "amount due"),
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
        "item_name": ("품목명", "description", "품명", "품목", "item"),
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
        "note": ("비고", "이동사유", "이동시유"),
    }

    def __init__(self) -> None:
        self.raw_snapshot = RawExtractionSnapshotService()

    def apply_to_document(self, document: Document, *, approval_note: str | None = None) -> dict[str, Any]:
        metadata = dict(document.workflow_metadata or {})
        raw = self.raw_snapshot.build(document, source="confirmed_review")
        pre_mapping = self.classification_pre_mapping(document, raw)
        mapping = self.map_raw(document, raw, mapping_source="confirmed_raw_data")

        metadata["raw_extraction"] = raw
        metadata["classification_pre_mapping"] = pre_mapping
        metadata["confirmed_raw_data"] = {
            **raw,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "approval_note": approval_note,
        }
        metadata.pop("semantic_mapping", None)
        metadata["confirmed_semantic_mapping"] = mapping
        metadata["business_fields"] = {**(metadata.get("business_fields") if isinstance(metadata.get("business_fields"), dict) else {}), **mapping.get("fields", {})}
        metadata["confirmed_semantic_mapping_version"] = mapping["version"]
        self._apply_document_type(document, mapping)
        document.workflow_metadata = metadata
        return mapping

    def classification_pre_mapping(self, document: Document, raw: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = raw or self.raw_snapshot.build(document, source="classification_pre_mapping")
        text = self._semantic_text(document, raw)
        headers = self._table_headers(raw)
        key_text = " ".join(str(item.get("key") or "") for item in raw.get("key_values") or [] if isinstance(item, dict))
        amount_present = bool(re.search(r"(?:금액|합계|공급가액|부가세|V\.?A\.?T|total|amount|price)", f"{text} {key_text}", flags=re.IGNORECASE))
        candidates: list[dict[str, Any]] = []
        for category, document_type, pattern in self.TYPE_LABELS:
            keyword_hits = re.findall(pattern, text, flags=re.IGNORECASE)
            header_hits = [header for header in headers if re.search(pattern, header, flags=re.IGNORECASE)]
            score = min(1.0, (0.45 if keyword_hits else 0) + (0.35 if header_hits else 0) + (0.1 if amount_present else 0))
            if score:
                candidates.append({
                    "category": category,
                    "document_type": document_type,
                    "score": round(score, 2),
                    "keyword_hits": list(dict.fromkeys(str(hit) for hit in keyword_hits[:8])),
                    "header_hits": header_hits[:8],
                })
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return {
            "version": "classification_pre_mapping_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "signals": {
                "title": document.title,
                "keyword_text_present": bool(text.strip()),
                "table_headers": headers,
                "amount_present": amount_present,
                "raw_key_value_count": len(raw.get("key_values") or []),
                "raw_table_count": len(raw.get("tables") or []),
            },
            "candidates": candidates[:5],
        }

    def map_raw(self, document: Document, raw: dict[str, Any], *, mapping_source: str = "raw_extraction") -> dict[str, Any]:
        text = self._semantic_text(document, raw)
        pre_mapping = self.classification_pre_mapping(document, raw)
        category, document_type = self._classify_type(document, text, pre_mapping)
        fields = self._base_fields(document)
        fields.update(self._fields_from_key_values(raw.get("key_values") or [], self.FIELD_ALIASES))
        if category == "pos_daily_settlement":
            fields.update(self._fields_from_key_values(raw.get("key_values") or [], self.POS_ALIASES))
            fields = self._normalize_pos_daily_fields(fields)
        fields = self._normalize_semantic_fields(fields, raw.get("key_values") or [], category)
        line_items = self._line_items_from_tables(raw.get("tables") or [])
        mapping_confidence = self._confidence(fields, line_items)
        return {
            "version": "semantic_mapping_v1",
            "mapping_source": mapping_source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "document_type": document_type,
            "category": category,
            "classification_pre_mapping": pre_mapping,
            "fields": fields,
            "line_items": line_items,
            "raw_table_count": len(raw.get("tables") or []),
            "raw_key_value_count": len(raw.get("key_values") or []),
            "mapping_confidence": mapping_confidence,
        }

    def _base_fields(self, document: Document) -> dict[str, Any]:
        fields = {
            "document_number": document.document_number,
            "sample_id": self._sample_id_from_filename(document.original_filename),
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
                if target in {"vendor_name", "customer_name"} and not self._is_party_name_key(target, raw_key):
                    continue
                if any(self._normalize_label(candidate) in normalized_key for candidate in candidates):
                    fields[target] = self._normalize_business_value(raw_value)
        return fields

    def _normalize_pos_daily_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        fields = dict(fields)
        if not fields.get("tax_amount") and fields.get("vat_amount"):
            fields["tax_amount"] = fields["vat_amount"]
        for total_key in ("payment_total", "actual_sales_amount", "net_sales_amount"):
            if fields.get(total_key):
                fields["document_total"] = fields[total_key]
                break
        return fields

    def _normalize_semantic_fields(self, fields: dict[str, Any], key_values: list[Any], category: str) -> dict[str, Any]:
        fields = dict(fields)
        raw_fields = self._raw_field_candidates(key_values)
        for target in ("sample_id", "document_number", "reference_number"):
            if raw_fields.get(target):
                fields[target] = raw_fields[target]
        for target in ("vendor_name", "customer_name"):
            if raw_fields.get(target):
                fields[target] = raw_fields[target]
        for target in ("issue_date", "due_date"):
            if raw_fields.get(target):
                fields[target] = raw_fields[target]
        fields = self._normalize_amount_fields(fields, raw_fields, category)
        return fields

    def _normalize_amount_fields(self, fields: dict[str, Any], raw_fields: dict[str, Any], category: str) -> dict[str, Any]:
        fields = dict(fields)
        if raw_fields.get("supply_amount"):
            fields["supply_amount"] = raw_fields["supply_amount"]
        if raw_fields.get("tax_amount"):
            fields["tax_amount"] = raw_fields["tax_amount"]

        total_priority = self._document_total_priority(category)
        for key in total_priority:
            if raw_fields.get(key):
                fields["document_total"] = raw_fields[key]
                break

        if not fields.get("document_total") and raw_fields.get("estimated_total"):
            fields["document_total"] = raw_fields["estimated_total"]
        if not fields.get("document_total"):
            inferred = self._sum_amounts(fields.get("supply_amount"), fields.get("tax_amount"))
            if inferred is not None:
                fields["document_total"] = str(inferred)
        if fields.get("document_total") and fields.get("supply_amount") and fields.get("tax_amount"):
            inferred = self._sum_amounts(fields.get("supply_amount"), fields.get("tax_amount"))
            total = self._decimal_from_text(str(fields.get("document_total")))
            supply = self._decimal_from_text(str(fields.get("supply_amount")))
            if inferred is not None and total is not None and supply is not None and total == supply and inferred != supply:
                fields["document_total"] = str(inferred)
        return fields

    def _document_total_priority(self, category: str) -> tuple[str, ...]:
        if category == "pos_daily_settlement":
            return ("payment_total", "actual_sales_amount", "net_sales_amount", "document_total")
        if category == "quotation":
            return ("estimated_total", "document_total")
        if category == "commercial_invoice":
            return ("total_usd", "document_total", "krw_converted")
        return ("document_total", "estimated_total")

    def _raw_field_candidates(self, key_values: list[Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        scores: dict[str, int] = {}
        for item in key_values:
            if not isinstance(item, dict):
                continue
            raw_key = str(item.get("key") or "")
            raw_value = item.get("value")
            if raw_value in (None, ""):
                continue
            normalized_key = self._normalize_label(raw_key)
            if target_value := self._identifier_field_from_key(normalized_key):
                fields[target_value] = self._normalize_identifier_value(raw_value)
                continue
            normalized_value = self._normalize_business_value(raw_value)
            if self._is_party_name_key("vendor_name", raw_key):
                self._set_scored_candidate(fields, scores, "vendor_name", normalized_value, self._party_candidate_score("vendor_name", raw_key, raw_value))
                continue
            if self._is_party_name_key("customer_name", raw_key):
                self._set_scored_candidate(fields, scores, "customer_name", normalized_value, self._party_candidate_score("customer_name", raw_key, raw_value))
                continue
            amount_field = self._amount_field_from_key(normalized_key)
            if amount_field:
                fields[amount_field] = normalized_value
                continue
            date_field = self._date_field_from_key(normalized_key)
            if date_field:
                fields[date_field] = normalized_value
        return fields

    def _set_scored_candidate(self, fields: dict[str, Any], scores: dict[str, int], target: str, value: object, score: int) -> None:
        if score < 0:
            return
        if score >= scores.get(target, -1):
            fields[target] = value
            scores[target] = score

    def _identifier_field_from_key(self, normalized_key: str) -> str | None:
        if normalized_key in {"샘플번호", "sampleid", "sampleno"}:
            return "sample_id"
        if normalized_key in {"문서번호", "documentno", "docno", "invoiceno"}:
            return "document_number"
        if normalized_key in {"참조번호", "관련문서번호", "원문서", "reference", "referenceno", "referencenumber"}:
            return "reference_number"
        return None

    def _normalize_identifier_value(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _is_party_name_key(self, target: str, raw_key: object) -> bool:
        normalized = self._normalize_label(raw_key)
        name_signal = any(signal in normalized for signal in ("상호", "업체명", "회사명", "거래처", "고객사", "seller", "vendor", "buyer", "customer"))
        blocked = any(signal in normalized for signal in ("사업자번호", "담당", "대표자", "주소", "전화", "번호"))
        if blocked:
            return False
        if target == "vendor_name":
            return (
                normalized in {"공급자", "공급처", "공급업체", "seller", "vendor"}
                or ("공급자" in normalized and name_signal)
                or "seller" in normalized
                or "vendor" in normalized
            )
        return (
            normalized in {"공급받는자", "고객사", "buyer", "customer"}
            or ("공급받는자" in normalized and name_signal)
            or "고객사" in normalized
            or "buyer" in normalized
            or "customer" in normalized
        )

    def _party_candidate_score(self, target: str, raw_key: object, raw_value: object) -> int:
        normalized_key = self._normalize_label(raw_key)
        text = re.sub(r"\s+", " ", str(raw_value or "")).strip()
        if not text:
            return -100
        if re.fullmatch(r"\d{3}-?\d{2}-?\d{5}", text):
            return -100
        if re.search(r"(작성일|발행일|거래일자|납기일|승인번호|문서번호|샘플번호|합계|금액|품목|수량)", text):
            return -80
        if len(text) > 60:
            return -20

        score = 0
        if target == "vendor_name":
            if any(signal in normalized_key for signal in ("공급받는자", "고객사", "buyer", "customer")):
                return -100
            if any(signal in normalized_key for signal in ("공급자", "공급처", "seller", "vendor")):
                score += 40
        else:
            if any(signal in normalized_key for signal in ("공급자", "공급처", "seller", "vendor")) and not any(
                signal in normalized_key for signal in ("공급받는자", "고객사", "buyer", "customer")
            ):
                return -100
            if any(signal in normalized_key for signal in ("공급받는자", "고객사", "buyer", "customer")):
                score += 40
        if "상호" in normalized_key or any(signal in normalized_key for signal in ("업체명", "회사명")):
            score += 20
        if "(주)" in text or text.startswith("주"):
            score += 10
        if re.search(r"[가-힣A-Za-z]", text):
            score += 5
        if re.search(r"\d", text):
            score -= 5
        if re.search(r"(담당|회계팀|구매팀|품질팀|검사자)", text):
            score -= 25
        return score

    def _amount_field_from_key(self, normalized_key: str) -> str | None:
        if normalized_key in {"공급가액", "공급기액", "공급기록", "공급가역", "궁급가액", "subtotal", "supplyamount"}:
            return "supply_amount"
        if normalized_key in {"세액", "세악", "새액", "사물에", "부가세", "vat", "v.a.t", "tax"}:
            return "tax_amount"
        if normalized_key in {"예상합계", "견적합계"}:
            return "estimated_total"
        if normalized_key in {"크레딧합계", "크레뒷합계", "반품합계", "조정합계", "차감합계"}:
            return "document_total"
        if normalized_key in {"결제합계", "실판매금액", "순판매금액"}:
            return {
                "결제합계": "payment_total",
                "실판매금액": "actual_sales_amount",
                "순판매금액": "net_sales_amount",
            }[normalized_key]
        if normalized_key in {
            "총합계",
            "송합계",
            "총함계",
            "합계금액",
            "합계",
            "함계",
            "합개",
            "총액",
            "청구금액",
            "결제금액",
            "amountdue",
            "total",
            "totalamount",
            "grandtotal",
            "invoicetotal",
        }:
            return "document_total"
        if normalized_key == "totalusd":
            return "total_usd"
        if normalized_key in {"krwconverted", "원화환산"}:
            return "krw_converted"
        return None

    def _date_field_from_key(self, normalized_key: str) -> str | None:
        if normalized_key in {"납기일", "지급기한", "paymentduedate", "duedate"}:
            return "due_date"
        if normalized_key in {"발행일", "작성일", "거래일자", "invoicedate", "견적일", "일자", "요청일"}:
            return "issue_date"
        return None

    def _sum_amounts(self, left: object, right: object) -> Decimal | None:
        left_number = self._decimal_from_text(str(left or ""))
        right_number = self._decimal_from_text(str(right or ""))
        if left_number is None or right_number is None:
            return None
        return left_number + right_number

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
                if self._is_semantic_line_item(mapped, row):
                    items.append(mapped)
        return items

    def _is_semantic_line_item(self, mapped: dict[str, Any], raw_row: dict[str, Any]) -> bool:
        if self._is_header_like_row(mapped, raw_row):
            return False
        if self._is_summary_like_row(mapped, raw_row):
            return False
        meaningful = {key for key, value in mapped.items() if value not in (None, "")}
        if meaningful <= {"line_number"}:
            return False
        return bool(
            mapped.get("item_name")
            or mapped.get("item_code")
            or (mapped.get("quantity") and (mapped.get("unit") or mapped.get("unit_price") or mapped.get("line_total") or mapped.get("supply_amount")))
        )

    def _is_header_like_row(self, mapped: dict[str, Any], raw_row: dict[str, Any]) -> bool:
        raw_text = self._row_text(raw_row)
        if not raw_text:
            return False
        header_terms = {
            "no",
            "번호",
            "품목",
            "품목명",
            "품명",
            "description",
            "규격",
            "규격코드",
            "품목코드",
            "수량",
            "단위",
            "단가",
            "금액",
            "공급가액",
            "세액",
            "합계",
            "비고",
        }
        tokens = {self._normalize_label(part) for part in re.split(r"[\s|,/]+", raw_text) if part.strip()}
        compact = self._normalize_label(raw_text)
        normalized_terms = {self._normalize_label(term) for term in header_terms}
        if tokens and tokens <= normalized_terms:
            return True
        return compact in normalized_terms or compact in {"no품목명규격코드수량단위단가금액", "no품목내부코드수량단위이동사유"}

    def _is_summary_like_row(self, mapped: dict[str, Any], raw_row: dict[str, Any]) -> bool:
        raw_text = self._row_text(raw_row)
        normalized = self._normalize_label(raw_text)
        if not normalized:
            return False
        if re.search(r"(총합계|합계금액|공급가액합계|세액합계|부가세|vat|subtotal|grandtotal|totalamount)", normalized, flags=re.IGNORECASE):
            return True
        item_name = self._normalize_label(mapped.get("item_name"))
        if item_name in {"합계", "총합계", "소계", "공급가액", "세액", "부가세", "vat", "total", "subtotal"}:
            return True
        return False

    def _row_text(self, row: dict[str, Any]) -> str:
        return " ".join(str(value or "") for value in row.values()).strip()

    def _table_column_target(self, column: str) -> str | None:
        normalized = self._normalize_label(column)
        for target, aliases in self.TABLE_COLUMN_ALIASES.items():
            if any(self._normalize_label(alias) == normalized for alias in aliases):
                return target
        for target, aliases in self.TABLE_COLUMN_ALIASES.items():
            if any(self._normalize_label(alias) in normalized for alias in aliases):
                return target
        return None

    def _sample_id_from_filename(self, filename: object) -> str | None:
        match = re.search(r"\b(DOC-\d{3,})\b", str(filename or ""), flags=re.IGNORECASE)
        return match.group(1).upper() if match else None

    def _classify_type(self, document: Document, text: str, pre_mapping: dict[str, Any] | None = None) -> tuple[str, str]:
        current_doc_type = getattr(document.document_type, "value", str(document.document_type or "general_document"))
        current_category = normalize_category_value(document.category) or current_doc_type
        candidates = pre_mapping.get("candidates") if isinstance(pre_mapping, dict) else []
        if isinstance(candidates, list) and candidates:
            top = candidates[0] if isinstance(candidates[0], dict) else {}
            if float(top.get("score") or 0) >= 0.45:
                return str(top.get("category") or current_category), str(top.get("document_type") or current_doc_type)
        for category, document_type, pattern in self.TYPE_LABELS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return category, document_type
        return current_category or "other", current_doc_type or "general_document"

    def _table_headers(self, raw: dict[str, Any]) -> list[str]:
        headers: list[str] = []
        for table in raw.get("tables") or []:
            if not isinstance(table, dict):
                continue
            for column in table.get("columns") or []:
                value = str(column or "").strip()
                if value and value not in headers:
                    headers.append(value)
        return headers

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
            normalized_date = self._normalize_date_text(stripped)
            if normalized_date:
                return normalized_date
            numeric = self._decimal_from_text(stripped)
            return str(numeric) if numeric is not None and re.search(r"\d", stripped) and not re.search(r"[A-Za-z가-힣]", stripped.replace(",", "")) else stripped
        return self._string_value(value)

    def _decimal_from_text(self, value: str) -> Decimal | None:
        stripped = str(value or "").strip()
        if re.fullmatch(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", stripped):
            return None
        compact = re.sub(r"[^0-9.\-]", "", stripped)
        if "," not in stripped and re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", compact):
            compact = compact.replace(".", "")
            try:
                return Decimal(compact)
            except InvalidOperation:
                return None
        tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?", stripped)
        if len(tokens) > 1:
            for token in reversed(tokens):
                parsed = self._decimal_from_text(token)
                if parsed is not None:
                    return parsed
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        if "," not in value and re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", cleaned):
            cleaned = cleaned.replace(".", "")
        if not cleaned or cleaned in {"-", "."}:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _normalize_date_text(self, value: str) -> str | None:
        if not re.fullmatch(r"\d{8}", value.strip()):
            return None
        compact = re.sub(r"[^0-9]", "", value)
        if len(compact) != 8 or not compact.startswith("20"):
            return None
        year = int(compact[:4])
        month_text = compact[4:6]
        day_text = compact[6:8]
        candidates: list[tuple[int, int]] = []
        for month in self._date_component_candidates(month_text, 1, 12):
            for day in self._date_component_candidates(day_text, 1, 31):
                if self._valid_date(year, month, day):
                    candidates.append((month, day))
        if not candidates:
            return None
        month, day = candidates[0]
        return f"{year:04d}-{month:02d}-{day:02d}"

    def _date_component_candidates(self, text: str, minimum: int, maximum: int) -> list[int]:
        value = int(text)
        candidates: list[int] = []
        if minimum <= value <= maximum:
            candidates.append(value)
        replacements = {"8": "0", "9": "0", "6": "0", "5": "0"}
        for index, char in enumerate(text):
            if char not in replacements:
                continue
            candidate_text = f"{text[:index]}{replacements[char]}{text[index + 1:]}"
            candidate = int(candidate_text)
            if minimum <= candidate <= maximum and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _valid_date(self, year: int, month: int, day: int) -> bool:
        try:
            datetime(year, month, day)
        except ValueError:
            return False
        return True

    def _string_value(self, value: object) -> object:
        if isinstance(value, (datetime, Decimal)):
            return str(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()  # type: ignore[no-any-return]
        return value
