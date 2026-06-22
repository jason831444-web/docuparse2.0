from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.canonical_schema import canonical_field_for_header


DATE_PATTERN = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{6})"
DOCUMENT_NUMBER_PATTERN = r"\b(?:PO|INV|DN|RCM|RC|TS|IQC|MV|QT|PM|POS|DOC)[-_]?[A-Z0-9]{0,8}[-_]?\d{2,6}(?:[-_]\d{1,6})?\b"
AMOUNT_PATTERN = r"(?:[-+]?\d{1,3}(?:,\d{3})+|[-+]?\d+)(?:\.\d+)?"
STRONG_POS_CONTEXT_PATTERN = re.compile(
    r"(?:POS|일\s*정산|정산|daily\s*sales|settlement|payment|cash|card|terminal|승인번호|카드사|현금|카드)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class DocumentPolicy:
    document_number_required: bool = True
    amount_allowed: bool = True
    tax_allowed: bool = True
    preferred_table_type: str = "line_items"
    header_ocr_supplement_allowed: bool = True


DOCUMENT_POLICIES: dict[str, DocumentPolicy] = {
    "inspection_report": DocumentPolicy(
        document_number_required=False,
        amount_allowed=False,
        tax_allowed=False,
        preferred_table_type="inspection_rows",
        header_ocr_supplement_allowed=False,
    ),
    "internal_transfer": DocumentPolicy(
        document_number_required=False,
        amount_allowed=False,
        tax_allowed=False,
        preferred_table_type="material_transfer_rows",
        header_ocr_supplement_allowed=False,
    ),
    "inventory_movement_document": DocumentPolicy(
        document_number_required=False,
        amount_allowed=False,
        tax_allowed=False,
        preferred_table_type="material_transfer_rows",
        header_ocr_supplement_allowed=False,
    ),
    "pos_daily_settlement": DocumentPolicy(
        document_number_required=False,
        amount_allowed=False,
        tax_allowed=False,
        preferred_table_type="settlement_summary",
        header_ocr_supplement_allowed=False,
    ),
    "purchase_memo": DocumentPolicy(document_number_required=False, header_ocr_supplement_allowed=False),
    "receipt": DocumentPolicy(document_number_required=False, header_ocr_supplement_allowed=False),
}


KEY_ALIASES: tuple[tuple[str, str, float], ...] = (
    ("document_number", r"(?:문서번호|발주번호|주문번호|견적번호|송장번호|Invoice\s*No\.?|PO\s*No\.?|Order\s*Ref|관리번호|전표번호|Ref\s*No\.?)", 0.9),
    ("document_date", r"(?:작성일|작성일자|발행일|요청일|납품일|검사일|거래일시|거래일|Date|Invoice\s*Date|Order\s*Date)", 0.84),
    ("due_date", r"(?:납기일|납품예정일|Due\s*Date|Delivery\s*Date)", 0.84),
    ("supplier_name", r"(?:공급자|공급업체|Vendor|Supplier)", 0.78),
    ("customer_name", r"(?:공급받는자|거래처|고객사|Customer|Store|Merchant|매장)", 0.78),
    ("source_warehouse", r"(?:출고창고|From\s*Warehouse|Source\s*Warehouse)", 0.84),
    ("destination_warehouse", r"(?:입고창고|To\s*Warehouse|Destination\s*Warehouse)", 0.84),
    ("requester", r"(?:요청자|담당자|담당|Contact|Requester)", 0.78),
    ("note", r"(?:비고|메모|특이사항|Note|Remark)", 0.72),
    ("total_amount", r"(?:합계금액|청구금액|총액|합계|Total)", 0.72),
    ("tax_amount", r"(?:세액|부가세|VAT|Tax)", 0.72),
    ("subtotal", r"(?:공급가액|소계|Subtotal|Supply\s*Amount)", 0.72),
)


def document_policy(document_type: str | None, raw_text: str | None = None) -> DocumentPolicy:
    doc_type = str(document_type or "").strip()
    policy = DOCUMENT_POLICIES.get(doc_type, DocumentPolicy())
    text = str(raw_text or "")
    if doc_type == "delivery_note" and no_price_signal(text):
        return DocumentPolicy(
            document_number_required=True,
            amount_allowed=False,
            tax_allowed=False,
            preferred_table_type="delivery_rows",
            header_ocr_supplement_allowed=True,
        )
    return policy


def no_price_signal(text: str | None) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return bool(re.search(r"(금액|단가|세액)(?:정보|항목)?(?:없음|미기재|제외)|수량확인용|단가미기재", compact))


class AiParsedDocumentBuilder:
    version = 1

    def build(
        self,
        *,
        raw_text: str,
        tables: list[dict[str, Any]] | None = None,
        document_type_hint: str | None = None,
        title: str | None = None,
        canonical_document: dict[str, Any] | None = None,
        source: str = "vl_raw_text_and_tables",
    ) -> dict[str, Any]:
        text = raw_text or ""
        policy = document_policy(document_type_hint, text)
        fields = self.extract_key_value_fields(text)
        table_sections = self.table_sections(tables or [], text)
        notes = self.extract_notes(text)
        blocked_candidates = self.blocked_candidates(fields, text, document_type_hint, policy)
        unmapped_fields = [field for field in fields if not field.get("normalized_key")]
        sections: list[dict[str, Any]] = []
        if fields:
            sections.append({"title": "일반 정보", "type": "key_value", "fields": fields})
        sections.extend(table_sections)
        if notes:
            sections.append({"title": "안내사항", "type": "notes", "items": notes, "source": "vl_raw_text"})
        warnings = self._warnings(fields, table_sections, blocked_candidates, policy)
        return {
            "version": self.version,
            "source": source,
            "title": title or self.infer_title(text, canonical_document),
            "document_type_hint": document_type_hint,
            "document_type_confidence": 0.72 if document_type_hint else None,
            "policy": {
                "document_number_required": policy.document_number_required,
                "amount_allowed": policy.amount_allowed,
                "tax_allowed": policy.tax_allowed,
                "preferred_table_type": policy.preferred_table_type,
                "header_ocr_supplement_allowed": policy.header_ocr_supplement_allowed,
            },
            "sections": sections,
            "unmapped_fields": unmapped_fields,
            "blocked_candidates": blocked_candidates,
            "warnings": warnings,
            "canonical_snapshot": canonical_document or {},
        }

    def extract_key_value_fields(self, raw_text: str) -> list[dict[str, Any]]:
        lines = _clean_lines(raw_text)
        fields: list[dict[str, Any]] = []
        seen: set[tuple[str | None, str, str]] = set()
        for field in self._party_fields_from_blocks(lines):
            key = (field.get("normalized_key"), field.get("key") or "", field.get("value") or "")
            if key in seen:
                continue
            seen.add(key)
            fields.append(field)
        for field in self._top_line_party_candidates(lines, raw_text):
            key = (field.get("normalized_key"), field.get("key") or "", field.get("value") or "")
            if key in seen:
                continue
            seen.add(key)
            fields.append(field)
        for index, line in enumerate(lines):
            field = self._field_from_line(line)
            if field is None:
                next_line = lines[index + 1] if index + 1 < len(lines) else ""
                field = self._field_from_adjacent_lines(line, next_line)
            if field is None:
                field = self._field_from_strong_pattern(line)
            if field is None:
                if self._looks_like_unmapped_key_value(line):
                    field = self._make_field(line.split()[0].rstrip(":："), " ".join(line.split()[1:]), None, 0.45, line, "unmapped")
                else:
                    continue
            key = (field.get("normalized_key"), field.get("key") or "", field.get("value") or "")
            if key in seen:
                continue
            seen.add(key)
            fields.append(field)
        return fields

    def _party_fields_from_blocks(self, lines: list[str]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for index, line in enumerate(lines[:80]):
            role = self._party_role_label(line)
            if not role:
                continue
            value = self._company_value_from_line(line)
            evidence = line
            if value is None:
                for next_line in lines[index + 1 : index + 4]:
                    if self._party_role_label(next_line) or self._line_starts_non_party_block(next_line):
                        break
                    value = self._company_value_from_line(next_line)
                    evidence = f"{line} {next_line}"
                    if value:
                        break
            value = _sanitize_party_candidate(value)
            if not value:
                continue
            normalized_key = "supplier_name" if role == "supplier" else "customer_name"
            fields.append(self._make_field("공급자" if role == "supplier" else "공급받는자", value, normalized_key, 0.82, evidence, "candidate"))
        return fields

    def _top_line_party_candidates(self, lines: list[str], raw_text: str) -> list[dict[str, Any]]:
        strong_pos = bool(STRONG_POS_CONTEXT_PATTERN.search(raw_text or ""))
        candidates: list[dict[str, Any]] = []
        for line in lines[:8]:
            value = _sanitize_party_candidate(line)
            if not value:
                continue
            if self._line_starts_non_party_block(line):
                continue
            if re.search(r"(영수증|receipt|거래명세서|발주서|견적서|납품서|세금계산서|검사|자재\s*이동|일\s*정산)", line, flags=re.IGNORECASE):
                continue
            status = "candidate" if strong_pos and re.search(r"(마트|상점|store|merchant|market)", line, flags=re.IGNORECASE) else "review_only"
            normalized_key = "merchant_name" if strong_pos else "party_name"
            confidence = 0.72 if strong_pos else 0.48
            candidates.append(self._make_field("상단 거래처 후보", value, normalized_key, confidence, line, status))
            break
        return candidates

    def _party_role_label(self, line: str) -> str | None:
        if re.search(r"(공급자|공급업체|매입처|vendor|supplier)", line, flags=re.IGNORECASE):
            return "supplier"
        if re.search(r"(공급받는자|고객사|거래처|수신|buyer|customer|bill\s*to|ship\s*to)", line, flags=re.IGNORECASE):
            return "customer"
        return None

    def _company_value_from_line(self, line: str) -> str | None:
        match = re.search(
            r"(?:상호|회사명|업체명|거래처명|고객사|수신|vendor|supplier|customer|buyer)\s*[:：]?\s*(?P<value>[^\n:：]{2,50})",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group("value")
        return None

    def _line_starts_non_party_block(self, line: str) -> bool:
        return bool(re.search(
            r"^(?:사업자|등록번호|대표|업태|업종|담당|담당자|주소|전화|이메일|품목|No\b|문서번호|작성일|발행일|견적일|납품일|검사일|거래일|합계|공급가액|세액)",
            line,
            flags=re.IGNORECASE,
        ))

    def table_sections(self, tables: list[dict[str, Any]], raw_text: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for index, table in enumerate(tables, start=1):
            if not isinstance(table, dict):
                continue
            columns = [str(column) for column in table.get("columns") or []]
            rows = self._table_rows(table, columns)
            if not columns and not rows:
                continue
            section: dict[str, Any] = {
                "title": table.get("title") or self._table_title(table, index),
                "type": "table",
                "columns": columns,
                "rows": rows,
                "source": table.get("source") or "vl_table",
                "table_type_guess": table.get("table_type") or table.get("table_type_guess"),
                "status": "candidate",
                "confidence": table.get("confidence") or 0.8,
            }
            for key in ("bbox", "block_bbox", "polygon", "quality", "provenance"):
                if table.get(key) not in (None, "", []):
                    section[key] = table.get(key)
            sections.append(section)
        if not sections:
            inferred = self._table_like_lines(raw_text)
            if inferred:
                sections.append(inferred)
        return sections

    def extract_notes(self, raw_text: str) -> list[str]:
        note_patterns = (
            r"금액.*(?:없음|미기재|제외)",
            r"(?:단가|세액).*(?:없음|미기재|제외)",
            r"수량\s*확인",
            r"검사\s*(?:결과|의견)",
            r"특이사항",
            r"비고",
            r"도장",
            r"현금\s*결제",
        )
        notes: list[str] = []
        seen: set[str] = set()
        for line in _clean_lines(raw_text):
            if not any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in note_patterns):
                continue
            if line in seen:
                continue
            seen.add(line)
            notes.append(line)
        return notes[:12]

    def blocked_candidates(
        self,
        fields: list[dict[str, Any]],
        raw_text: str,
        document_type_hint: str | None,
        policy: DocumentPolicy | None = None,
    ) -> list[dict[str, Any]]:
        policy = policy or document_policy(document_type_hint, raw_text)
        blocked: list[dict[str, Any]] = []
        for field in fields:
            normalized_key = field.get("normalized_key")
            if normalized_key in {"total_amount", "subtotal", "tax_amount"} and (
                not policy.amount_allowed or (normalized_key == "tax_amount" and not policy.tax_allowed)
            ):
                blocked.append({
                    **field,
                    "status": "blocked",
                    "risk": "amount_not_allowed_for_document_type",
                    "reason": f"{document_type_hint or 'document'}_policy",
                })
        return blocked

    def document_number_candidates(self, raw_text: str) -> list[dict[str, Any]]:
        return [field for field in self.extract_key_value_fields(raw_text) if field.get("normalized_key") == "document_number"]

    def should_skip_header_ocr(self, *, raw_text: str, document_type_hint: str | None = None) -> dict[str, Any]:
        policy = document_policy(document_type_hint, raw_text)
        candidates = self.document_number_candidates(raw_text)
        if candidates:
            return {
                "skip": True,
                "reason": "ai_parsed_document_number_candidate_found",
                "candidate_count": len(candidates),
                "policy": "ai_parsed_document",
            }
        if not policy.document_number_required or not policy.header_ocr_supplement_allowed:
            return {
                "skip": True,
                "reason": "document_type_policy_document_number_optional",
                "candidate_count": 0,
                "policy": "ai_parsed_document",
            }
        return {"skip": False, "reason": "document_number_missing_and_policy_requires_check", "candidate_count": 0, "policy": "ai_parsed_document"}

    def infer_title(self, raw_text: str, canonical_document: dict[str, Any] | None = None) -> str | None:
        if canonical_document and canonical_document.get("title"):
            return str(canonical_document["title"])
        for line in _clean_lines(raw_text)[:8]:
            compact = re.sub(r"\s+", "", line)
            if 2 <= len(compact) <= 28 and re.search(r"(발주서|견적서|납품서|세금계산서|거래명세서|검사|영수증|자재\s*이동|일정산|Invoice|Receipt)", line, flags=re.IGNORECASE):
                return line
        return None

    def _field_from_line(self, line: str) -> dict[str, Any] | None:
        for normalized_key, key_pattern, confidence in KEY_ALIASES:
            match = re.search(rf"(?P<key>{key_pattern})\s*[:：]?\s*(?P<value>.+)", line, flags=re.IGNORECASE)
            if not match:
                continue
            value = match.group("value").strip(" :：")
            if not value or re.fullmatch(match.group("key").strip(), value, flags=re.IGNORECASE):
                continue
            return self._make_field(match.group("key"), value, normalized_key, confidence, line, "candidate")
        return None

    def _field_from_adjacent_lines(self, line: str, next_line: str) -> dict[str, Any] | None:
        if not next_line:
            return None
        for normalized_key, key_pattern, confidence in KEY_ALIASES:
            if re.fullmatch(rf"{key_pattern}\s*[:：]?", line, flags=re.IGNORECASE):
                value = next_line
                if normalized_key in {"supplier_name", "customer_name"}:
                    value = _sanitize_party_candidate(value) or next_line
                return self._make_field(line.strip(" :："), value, normalized_key, max(0.5, confidence - 0.08), f"{line} {next_line}", "candidate")
        return None

    def _field_from_strong_pattern(self, line: str) -> dict[str, Any] | None:
        document_number = re.search(DOCUMENT_NUMBER_PATTERN, line, flags=re.IGNORECASE)
        if document_number:
            return self._make_field("문서번호 후보", document_number.group(0), "document_number", 0.76, line, "candidate")
        date_match = re.search(DATE_PATTERN, line)
        if date_match and re.search(r"(작성|발행|요청|납품|검사|거래|date)", line, flags=re.IGNORECASE):
            return self._make_field("날짜 후보", date_match.group(0), "document_date", 0.68, line, "candidate")
        return None

    def _make_field(self, key: str, value: str, normalized_key: str | None, confidence: float, evidence: str, status: str) -> dict[str, Any]:
        value_text = _clean_scalar_value(value)
        return {
            "key": str(key).strip(" :："),
            "value": value_text,
            "normalized_key": normalized_key,
            "confidence": round(confidence, 3),
            "source": "vl_raw_text",
            "evidence": evidence,
            "status": status,
            "bbox": None,
        }

    def _looks_like_unmapped_key_value(self, line: str) -> bool:
        if len(line) > 80 or len(line.split()) > 6:
            return False
        return bool(re.search(r"[:：]", line) or re.match(r"^[가-힣A-Za-z/ ]{2,16}\s+\S{2,}", line))

    def _table_rows(self, table: dict[str, Any], columns: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(table.get("rows") or [], start=1):
            cells = self._row_cells(row, columns)
            if not cells:
                continue
            rows.append({
                "row_index": row_index,
                "cells": cells,
                "canonical_cells": self._canonical_cells(cells),
                "source": table.get("source") or "vl_table",
                "status": "candidate",
                "confidence": row.get("confidence") if isinstance(row, dict) and row.get("confidence") is not None else 0.78,
            })
        return rows

    def _row_cells(self, row: Any, columns: list[str]) -> dict[str, Any]:
        if isinstance(row, dict):
            raw_cells = row.get("raw_cells")
            if isinstance(raw_cells, dict) and raw_cells:
                return {str(key): value for key, value in raw_cells.items() if value not in (None, "")}
            preferred = {key: value for key, value in row.items() if key not in {"confidence", "review_flags", "validation_warnings"} and value not in (None, "", [])}
            if columns:
                return {column: preferred.get(column, preferred.get(canonical_field_for_header(column) or column, "")) for column in columns if preferred.get(column, preferred.get(canonical_field_for_header(column) or column, "")) not in (None, "")}
            return {str(key): value for key, value in preferred.items() if not isinstance(value, (dict, list))}
        if isinstance(row, list):
            return {columns[index] if index < len(columns) else f"column_{index + 1}": value for index, value in enumerate(row) if value not in (None, "")}
        return {}

    def _canonical_cells(self, cells: dict[str, Any]) -> dict[str, Any]:
        canonical: dict[str, Any] = {}
        for header, value in cells.items():
            field = canonical_field_for_header(header)
            if field:
                canonical[field] = value
        return canonical

    def _table_title(self, table: dict[str, Any], index: int) -> str:
        table_type = str(table.get("table_type") or table.get("table_type_guess") or "")
        if table_type in {"incoming_inspection", "inspection_rows"}:
            return "검사/입고 표"
        if table_type in {"material_transfer_rows", "internal_transfer"}:
            return "자재 이동 목록"
        return f"표 {index}"

    def _table_like_lines(self, raw_text: str) -> dict[str, Any] | None:
        lines = _clean_lines(raw_text)
        header_index: int | None = None
        for index, line in enumerate(lines):
            canonical_hits = {canonical_field_for_header(part) for part in re.split(r"\s{1,}|\|", line) if part}
            if "item_name" in canonical_hits and ({"quantity", "received_quantity", "requested_quantity", "delivered_quantity"} & canonical_hits):
                header_index = index
                break
        if header_index is None:
            return None
        rows = []
        for line in lines[header_index + 1 : header_index + 8]:
            if not re.match(r"^\d{1,3}\s+", line):
                continue
            rows.append({
                "row_index": len(rows) + 1,
                "cells": {"raw_line": line},
                "canonical_cells": {},
                "source": "vl_raw_text_table_like_lines",
                "status": "candidate",
                "confidence": 0.45,
            })
        if not rows:
            return None
        return {
            "title": "원문 표 후보",
            "type": "table",
            "columns": ["raw_line"],
            "rows": rows,
            "source": "vl_raw_text_table_like_lines",
            "status": "candidate",
            "confidence": 0.45,
        }

    def _warnings(self, fields: list[dict[str, Any]], table_sections: list[dict[str, Any]], blocked_candidates: list[dict[str, Any]], policy: DocumentPolicy) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if not table_sections:
            warnings.append({"code": "ai_parsed_document_no_table_section", "severity": "info"})
        if not any(field.get("normalized_key") == "document_number" for field in fields) and policy.document_number_required:
            warnings.append({"code": "ai_parsed_document_document_number_candidate_missing", "severity": "warning"})
        if blocked_candidates:
            warnings.append({"code": "ai_parsed_document_blocked_candidates_present", "severity": "warning", "count": len(blocked_candidates)})
        return warnings


def _clean_lines(raw_text: str) -> list[str]:
    cleaned: list[str] = []
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        folded = line.casefold()
        if re.search(r"(^|\s)/(?:workspace|tmp|var|users)/", folded) or re.search(
            r"(vl_remote_uploads|vl_rendered_pages|uploads/|document_details/).+\.(?:pdf|png|jpe?g|webp|tiff?)",
            folded,
        ):
            continue
        cleaned.append(line)
    return cleaned


def _sanitize_party_candidate(value: Any) -> str | None:
    text = _clean_scalar_value(value)
    if not text:
        return None
    text = re.sub(r"^(?:상호|회사명|업체명|거래처명|공급자|공급업체|공급받는자|고객사|수신)\s*[:：]?\s*", "", text).strip()
    text = re.sub(r"\s*(?:사업자|등록번호|대표|업태|업종|담당|담당자|주소|전화|이메일|품목|No\b|문서번호|작성일|발행일|견적일|납품일|검사일|거래일|합계|공급가액|세액).*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(?:\(?주\)?|주식회사)\s*", "", text).strip()
    text = re.sub(r"\s*(?:\(?주\)?|주식회사)$", "", text).strip()
    if not text or len(text) > 40:
        return None
    if re.search(r"[/\\]|^\d|[_]{2,}|-{3,}|(?:20\d{2}[./-]\d{1,2})", text):
        return None
    if re.search(r"(doc[_ -]?title|sample|샘플|생품|생플|문서번호|운서번호|발주서|견적서|겨적서|납품서|세금\s*계산서|거래\s*명세서|입고\s*검사|검사\s*기록|자재\s*이동|영수증|일\s*정산|합계|공급가액|세액|품목|수량|단가|비고|출고창고|입고창고|창고|warehouse|purchase\s*order|quotation|invoice)", text, flags=re.IGNORECASE):
        return None
    if re.search(r"(경기도|서울|부산|인천|대구|광주|대전|울산|세종|충청|전라|경상|강원|제주|시흥시|공단로|대로|번길|주소)", text):
        return None
    if not re.search(r"[가-힣A-Za-z]", text):
        return None
    return text


def _clean_scalar_value(value: Any) -> str:
    text = str(value or "").strip(" :：")
    text = re.split(r"(?:\\n|\n|\r)", text, maxsplit=1)[0].strip(" :：")
    return text
