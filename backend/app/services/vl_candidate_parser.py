from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models.document import DocumentType
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
        doc_type = self.parser._guess_document_type(str(text or ""), filename)
        cleaned = self._clean_text(text, doc_type=doc_type)
        if not cleaned:
            return None
        parsed = self.parser.parse(cleaned, filename)
        parsed.line_items = self._line_items_for_visible_columns_only(cleaned, doc_type, parsed.line_items)
        handwritten_items = self._extract_handwritten_freeform_items(cleaned, parsed.document_type)
        if (
            not self._has_explicit_table_header(cleaned, parsed.document_type)
            and self._should_prefer_handwritten_items(handwritten_items, parsed.line_items)
        ):
            parsed.line_items = handwritten_items
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

    def _clean_text(self, text: str, doc_type: Any | None = None) -> str:
        text = self._normalize_vl_text(text)
        lines = []
        for raw in (text or "").splitlines():
            line = " ".join(raw.strip().split())
            if line:
                lines.extend(self._expand_inline_table_line(line, doc_type))
        return "\n".join(lines)

    def _normalize_vl_text(self, text: str) -> str:
        normalized = str(text or "")
        normalized = normalized.replace("×", "x").replace("\\times", "x")
        normalized = re.sub(r"\\begin\{array\}\{[^}]*\}", "\n", normalized)
        normalized = normalized.replace("\\end{array}", "\n")
        normalized = normalized.replace("\\\\", "\n")
        normalized = normalized.replace("\\quad", " ")
        normalized = re.sub(r"\\text\{([^{}]*)\}", r"\1", normalized)
        normalized = normalized.replace("$$", "\n").replace("$", " ")
        normalized = re.sub(
            r"(?<=\d)\s*[xX]\s*(?=\d)",
            lambda match: "X" if "X" in match.group(0) else "x",
            normalized,
        )
        replacements = {
            "거래멈세서": "거래명세서",
            "거래명세": "거래명세서",
            "자제 리스크": "자재 리스트",
            "자제 리스트": "자재 리스트",
            "거리처": "거래처",
            "검사수감": "검사수량",
            "함께": "합격",
            "보득": "보류",
            "육각분트": "육각볼트",
            "육각폴트": "육각볼트",
            "폴트": "볼트",
            "육각볼트": "육각볼트",
            "스프장와야": "스프링와샤",
            "스프렁와샤": "스프링와샤",
            "스프링와야": "스프링와샤",
            "브라컷": "브라켓",
            "봉제": "봉재",
            "545C": "S45C",
            "站到": "합계",
            "합게": "합계",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        normalized = normalized.replace("거래명세서서", "거래명세서")
        cleaned_lines: list[str] = []
        for raw in normalized.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line in {"doc_title", "display_formula"}:
                continue
            line = re.sub(r"^#+\s*", "", line)
            line = re.sub(r"^합계\s*[:：]", "총액:", line)
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines)

    def _expand_inline_table_line(self, line: str, doc_type: Any | None = None) -> list[str]:
        """Restore row boundaries when VL returns a whole table as one line."""

        tokens = str(line or "").split()
        if len(tokens) < 12:
            return [line]
        header_start = self._inline_header_start(tokens)
        if header_start is None:
            return [line]
        header_end = self._inline_header_end(tokens, header_start)
        if header_end <= header_start or header_end >= len(tokens):
            return [line]

        prefix = " ".join(tokens[:header_start]).strip()
        header = " ".join(tokens[header_start:header_end]).strip()
        table_tokens = tokens[header_end:]
        summary_start = self._inline_summary_start(table_tokens)
        summary = ""
        if summary_start is not None:
            summary = " ".join(table_tokens[summary_start:]).strip()
            table_tokens = table_tokens[:summary_start]

        rows = self._split_inline_table_rows(table_tokens, doc_type)
        if not rows:
            return [line]
        expanded = [value for value in (prefix, header, *rows, summary) if value]
        return expanded or [line]

    def _inline_header_start(self, tokens: list[str]) -> int | None:
        best_index: int | None = None
        best_hits = 0
        for index, token in enumerate(tokens):
            if not self._is_inline_header_token(token):
                continue
            hits = 0
            for candidate in tokens[index : min(len(tokens), index + 14)]:
                if self._is_inline_header_token(candidate):
                    hits += 1
            if hits > best_hits:
                best_hits = hits
                best_index = index
        return best_index if best_index is not None and best_hits >= 4 else None

    def _inline_header_end(self, tokens: list[str], start: int) -> int:
        index = start
        last_header = start
        while index < len(tokens):
            token = tokens[index]
            if self._is_inline_header_token(token):
                last_header = index + 1
                index += 1
                continue
            if index + 1 < len(tokens) and self._is_inline_header_token(f"{token} {tokens[index + 1]}"):
                last_header = index + 2
                index += 2
                continue
            break
        return last_header

    def _is_inline_header_token(self, token: str) -> bool:
        normalized = re.sub(r"[\s_/-]+", "", str(token or "").casefold())
        header_tokens = {
            "no",
            "품목명",
            "품명",
            "반품품목",
            "품목코드",
            "문서품목코드",
            "내부품목코드",
            "거래처품목코드",
            "item",
            "itemname",
            "itemcode",
            "description",
            "vendorsku",
            "vendor",
            "sku",
            "lot",
            "lotno",
            "규격",
            "spec",
            "specification",
            "수량",
            "요청수량",
            "발주수량",
            "납품수량",
            "입고수량",
            "합격수량",
            "불량수량",
            "잔량",
            "qty",
            "quantity",
            "단위",
            "unit",
            "단가",
            "unitprice",
            "price",
            "공급가액",
            "subtotal",
            "세액",
            "합계금액",
            "linetotal",
            "amount",
            "tax",
            "total",
            "판정",
            "비고",
        }
        return normalized in header_tokens

    def _inline_summary_start(self, tokens: list[str]) -> int | None:
        for index, token in enumerate(tokens):
            normalized = re.sub(r"[\s_/-]+", "", str(token or "").casefold())
            if normalized in {"공급가액", "세액", "총액", "합계", "합계금액", "subtotal", "tax", "vat", "total"}:
                return index
        return None

    def _split_inline_table_rows(self, tokens: list[str], doc_type: Any | None = None) -> list[str]:
        rows: list[str] = []
        index = 0
        while index < len(tokens):
            candidates: list[tuple[int, int, str]] = []
            max_end = min(len(tokens), index + 16)
            for end in range(index + 4, max_end + 1):
                row_text = " ".join(tokens[index:end])
                item = self.parser._vl_inline_table_item_from_row(row_text, doc_type)
                if not item:
                    continue
                score = self._inline_row_score(item)
                if score >= 4:
                    candidates.append((score, end, row_text))
            if not candidates:
                remainder = " ".join(tokens[index:]).strip()
                if remainder:
                    rows.append(remainder)
                break
            candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
            score, end, row_text = candidates[0]
            rows.append(row_text)
            index = end
        return rows

    def _inline_row_score(self, item: dict[str, Any]) -> int:
        score = 0
        for field in ("item_name", "unit", "quantity", "unit_price", "supply_amount", "tax_amount", "line_total"):
            if item.get(field) not in (None, "", []):
                score += 1
        if item.get("document_item_code") or item.get("item_code"):
            score += 1
        return score

    def _line_items_for_visible_columns_only(
        self,
        text: str,
        doc_type: Any | None,
        fallback_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        inline_items = self.parser._extract_vl_inline_table_items(text.splitlines(), doc_type)
        if not self._has_hidden_or_truncated_amount_signal(text):
            if self._should_prefer_inline_items(inline_items, fallback_items):
                return inline_items
            return fallback_items
        if not inline_items:
            return fallback_items
        return [self._suppress_hidden_positive_line_amount(item) for item in inline_items]

    def _should_prefer_inline_items(
        self,
        inline_items: list[dict[str, Any]],
        fallback_items: list[dict[str, Any]],
    ) -> bool:
        if not inline_items:
            return False
        if not fallback_items:
            return True
        if len(inline_items) < len(fallback_items):
            return False
        fallback_warning_count = sum(1 for item in fallback_items if item.get("validation_warnings"))
        inline_warning_count = sum(1 for item in inline_items if item.get("validation_warnings"))
        if fallback_warning_count and inline_warning_count <= fallback_warning_count:
            return True
        inline_amount_fields = sum(
            1
            for item in inline_items
            for field in ("supply_amount", "tax_amount", "line_total")
            if item.get(field) not in (None, "", [])
        )
        fallback_amount_fields = sum(
            1
            for item in fallback_items
            for field in ("supply_amount", "tax_amount", "line_total")
            if item.get(field) not in (None, "", [])
        )
        return inline_amount_fields > fallback_amount_fields

    def _should_prefer_handwritten_items(
        self,
        handwritten_items: list[dict[str, Any]],
        fallback_items: list[dict[str, Any]],
    ) -> bool:
        if not handwritten_items:
            return False
        if not fallback_items:
            return True
        fallback_names = {str(item.get("item_name") or "").strip() for item in fallback_items}
        handwritten_names = {str(item.get("item_name") or "").strip() for item in handwritten_items}
        header_like_fallbacks = sum(1 for name in fallback_names if self._looks_like_table_header(name))
        if header_like_fallbacks:
            return True
        fallback_score = self._structured_line_item_score(fallback_items)
        handwritten_score = self._structured_line_item_score(handwritten_items)
        if fallback_score >= handwritten_score and fallback_score >= max(3, len(fallback_items) * 2):
            return False
        if len(handwritten_names - fallback_names) >= max(1, len(fallback_names)):
            return True
        return len(handwritten_items) > len(fallback_items)

    def _structured_line_item_score(self, items: list[dict[str, Any]]) -> int:
        score = 0
        structured_fields = (
            "document_item_code",
            "item_code",
            "specification",
            "quantity",
            "unit",
            "unit_price",
            "supply_amount",
            "tax_amount",
            "line_total",
            "received_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "inspection_result",
        )
        for item in items or []:
            if not item.get("item_name"):
                continue
            score += 1
            for field in structured_fields:
                if item.get(field) not in (None, "", []):
                    score += 1
        return score

    def _has_explicit_table_header(self, text: str, doc_type: Any | None = None) -> bool:
        lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
        for line in lines:
            if self.parser._looks_like_vl_inline_table_header(line, doc_type):
                return True
        return bool(
            re.search(
                r"(?:^|\n)[^\n]*(?:품목|품목명|품명|반품품목|Item\s+Description|Description|"
                r"품목\s*코드|문서품목코드|Vendor\s+SKU)[^\n]*(?:수량|Qty|Quantity|단가|공급가액|합계|세액|"
                r"입고수량|합격수량|불량수량)",
                text or "",
                flags=re.IGNORECASE,
            )
        )

    def _extract_handwritten_freeform_items(
        self,
        text: str,
        doc_type: Any | None,
    ) -> list[dict[str, Any]]:
        lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
        items: list[dict[str, Any]] = []
        inspection_item = self._extract_handwritten_inspection_item(lines)
        if inspection_item:
            items.append(inspection_item)
        for line in lines:
            item = self._handwritten_row_item_from_line(line, doc_type)
            if item:
                items.append(item)
        return self._dedupe_handwritten_items(items)

    def _extract_handwritten_inspection_item(self, lines: list[str]) -> dict[str, Any] | None:
        product = self._handwritten_labeled_value(lines, ["품명", "품목명"])
        quantity_text = self._handwritten_labeled_value(lines, ["검사수량", "입고수량"])
        if not product or not quantity_text:
            return None
        quantity_match = re.search(r"(\d[\d,]*)", quantity_text)
        if not quantity_match:
            return None
        item_name, specification = self._split_handwritten_identity(product)
        item: dict[str, Any] = {
            "item_name": item_name,
            "quantity": quantity_match.group(1),
            "received_quantity": quantity_match.group(1),
            "validation_warnings": ["handwritten_vl_candidate", "handwritten_inspection_requires_review"],
            "_provenance": {"source_type": "vl_source", "mode": "handwritten_label_parse"},
        }
        if specification:
            item["specification"] = specification
        accepted, hold = self._extract_handwritten_accept_hold(lines)
        if accepted is not None:
            item["accepted_quantity"] = accepted
        if hold is not None:
            item["hold_quantity"] = hold
            item["validation_warnings"].append("hold_quantity_requires_review")
        return self.parser._normalize_line_item(item)

    def _handwritten_labeled_value(self, lines: list[str], labels: list[str]) -> str | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        for index, line in enumerate(lines):
            match = re.search(rf"(?:^|\s)(?:{label_pattern})\s*[:：]?\s*(.+)$", line, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip(" -:：")
                if value:
                    return value
            if re.sub(r"[\s:：]+", "", line) in {re.sub(r"[\s:：]+", "", label) for label in labels}:
                for candidate in lines[index + 1 : min(index + 4, len(lines))]:
                    if candidate and not re.search(r"[:：]$", candidate):
                        return candidate.strip()
        return None

    def _extract_handwritten_accept_hold(self, lines: list[str]) -> tuple[str | None, str | None]:
        joined = " ".join(lines)
        accepted_match = re.search(r"합격\s*(\d[\d,]*)", joined)
        hold_match = re.search(r"(?:보류|보류수량)\s*(\d[\d,]*)", joined)
        return (
            accepted_match.group(1) if accepted_match else None,
            hold_match.group(1) if hold_match else None,
        )

    def _handwritten_row_item_from_line(self, line: str, doc_type: Any | None) -> dict[str, Any] | None:
        text = re.sub(r"^\s*\d+\)\s*", "", line.strip())
        text = re.sub(r"^\s*\d+\.\s*", "", text)
        text = re.sub(r"^\s*[+\-•·]\s*", "", text)
        if self._should_skip_handwritten_row(text):
            return None
        match = re.match(
            r"^(?P<body>.+?)\s+"
            r"(?P<quantity>\d[\d,]*)\s*(?P<unit>EA|ea|개|장|본|봉)?"
            r"(?:\s+(?P<unit_price>[-+]?\d[\d,]*(?:\.\d+)?))?\s*$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        body = match.group("body").strip()
        if not re.search(r"[A-Za-z가-힣]", body):
            return None
        if self._looks_like_date_or_note_row(text):
            return None
        item_name, specification = self._split_handwritten_identity(body)
        if not item_name or self._looks_like_business_label(item_name):
            return None
        item: dict[str, Any] = {
            "item_name": item_name,
            "quantity": match.group("quantity"),
            "validation_warnings": ["handwritten_vl_candidate", "handwritten_requires_review"],
            "_provenance": {"source_type": "vl_source", "mode": "handwritten_freeform_row"},
        }
        if specification:
            item["specification"] = specification
        if match.group("unit"):
            item["unit"] = match.group("unit").upper() if match.group("unit").lower() == "ea" else match.group("unit")
        unit_price = match.group("unit_price")
        if unit_price is not None and doc_type in {
            DocumentType.transaction_statement,
            DocumentType.invoice,
            DocumentType.purchase_order,
            DocumentType.quotation,
        }:
            item["unit_price"] = unit_price
            item["validation_warnings"].append("line_total_not_visible_do_not_infer")
        elif unit_price is not None:
            item["validation_warnings"].append("trailing_number_requires_review")
        else:
            item["validation_warnings"].append("handwritten_amount_missing_or_not_applicable")
        return self.parser._normalize_line_item(item)

    def _should_skip_handwritten_row(self, text: str) -> bool:
        if not text:
            return True
        normalized = re.sub(r"\s+", "", text)
        if re.search(
            r"^(?:제목|날짜|일자|업체|거래처|받는곳|현장|담당|비고|메모|서명|납기|총|합계|공급가액|세액|"
            r"품명|품목명|검사수량|치수|표면|외관|수량확인|합격|보류|청구금액|차감합계|조정합계|판매총액|실판매금액|판입금액|"
            r"온라인결제|카드결제|현금결제|입금액|결제금액)",
            normalized,
        ):
            return True
        if text in {"간이 검사 기록", "거래명세서", "납품서", "발주 메모", "입고 확인", "자재 리스트"}:
            return True
        return False

    def _looks_like_date_or_note_row(self, text: str) -> bool:
        return bool(
            re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{1,2,4}", text)
            or re.search(r"(이상\s*없음|먼저|부탁|급함|처리|완료|예정)", text)
        )

    def _split_handwritten_identity(self, value: str) -> tuple[str, str | None]:
        text = " ".join(str(value or "").replace("×", "x").split()).strip(" -")
        tokens = text.split()
        if len(tokens) < 2:
            return text, None
        spec_tokens: list[str] = []
        while tokens and self._looks_like_handwritten_spec_token(tokens[-1]):
            spec_tokens.insert(0, tokens.pop())
            if len(spec_tokens) >= 2:
                break
        if not tokens:
            return text, None
        return " ".join(tokens), " ".join(spec_tokens) if spec_tokens else None

    def _looks_like_handwritten_spec_token(self, token: str) -> bool:
        value = str(token or "").strip()
        return bool(
            re.fullmatch(r"(?:M\d+(?:x\d+)?|\d+(?:x\d+){1,2}|\d+(?:T|t|파이|mm)|\d{3,5})", value)
            or re.fullmatch(r"[A-Z]{2,}\d+[A-Z0-9-]*", value)
        )

    def _looks_like_business_label(self, value: str) -> bool:
        return bool(re.fullmatch(r"(품목|품명|수량|단가|합계|거래처|업체|담당|비고)", str(value or "").strip()))

    def _dedupe_handwritten_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any, Any]] = set()
        for item in items:
            key = (item.get("item_name"), item.get("specification"), item.get("quantity"))
            if not item.get("item_name") or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _has_hidden_or_truncated_amount_signal(self, text: str) -> bool:
        return bool(
            re.search(
                r"(hidden|clipped|cropped|truncated|not\s+visible|visual(?:ly)?\s+confirmed|"
                r"잘림|잘려|가려|오른쪽|보이지\s*않|공급\s*$|단가\s+공(?:\s|$))",
                text or "",
                flags=re.IGNORECASE | re.MULTILINE,
            )
        )

    def _suppress_hidden_positive_line_amount(self, item: dict[str, Any]) -> dict[str, Any]:
        safe_item = dict(item)
        supply = self._decimal_value(safe_item.get("supply_amount"))
        if (
            supply is not None
            and supply > 0
            and safe_item.get("unit_price") in (None, "", [])
            and safe_item.get("quantity") not in (None, "", [])
            and safe_item.get("unit") not in (None, "", [])
        ):
            safe_item["unit_price"] = safe_item.pop("supply_amount")
            warnings = list(safe_item.get("validation_warnings") or [])
            for warning in ("missing_line_amount", "row_amount_hidden_do_not_infer"):
                if warning not in warnings:
                    warnings.append(warning)
            safe_item["validation_warnings"] = warnings
        return safe_item

    def _compact_document(self, parsed: ParsedDocument) -> dict[str, Any]:
        currency = parsed.currency
        if self._is_amountless_quantity_document(parsed):
            currency = None
        return {
            "document_type": self._safe_value(parsed.document_type),
            "document_number": parsed.document_number,
            "vendor_name": parsed.vendor_name or parsed.merchant_name,
            "customer_name": parsed.customer_name,
            "issue_date": self._safe_value(parsed.issue_date or parsed.extracted_date),
            "due_date": self._safe_value(parsed.due_date),
            "currency": currency,
            "subtotal": self._safe_nonnegative_amount(parsed.subtotal),
            "tax": self._safe_nonnegative_amount(parsed.tax),
            "total": self._safe_nonnegative_amount(parsed.extracted_amount),
            "business_fields": self._safe_value(parsed.business_fields),
        }

    def _is_amountless_quantity_document(self, parsed: ParsedDocument) -> bool:
        if any(getattr(parsed, field, None) is not None for field in ("subtotal", "tax", "extracted_amount")):
            return False
        for item in parsed.line_items or []:
            if any(item.get(field) not in (None, "", []) for field in ("unit_price", "supply_amount", "tax_amount", "line_total")):
                return False
        doc_type = parsed.document_type.value if isinstance(parsed.document_type, DocumentType) else str(parsed.document_type or "")
        return doc_type in {
            "delivery_note",
            "inspection_report",
            "general_document",
            "packing_list",
            "other",
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
            "inspection_result",
            "unit",
            "unit_price",
            "supply_amount",
            "tax_amount",
            "line_total",
            "validation_warnings",
            "review_flags",
            "_provenance",
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

        for field_name, value in (
            ("subtotal", parsed.subtotal),
            ("tax", parsed.tax),
            ("total", parsed.extracted_amount),
        ):
            numeric_value = self._decimal_value(value)
            if numeric_value is not None and numeric_value < 0:
                issues.append(
                    {
                        "code": "vl_candidate_negative_document_amount_suppressed",
                        "severity": "warn",
                        "field": field_name,
                        "actual_value": str(numeric_value),
                        "message": "A negative document-level amount was suppressed; negative values may remain only as row-level adjustments.",
                    }
                )

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
            if self._row_boundary_needs_fax_review(text):
                issues.append(
                    {
                        "code": "vl_candidate_fax_row_boundary_uncertain",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "Fax-like or O/0-confusable source signals are present; row boundaries and numeric cells require review.",
                    }
                )
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
            if self._delivery_row_has_hidden_remaining_quantity(text, item):
                issues.append(
                    {
                        "code": "vl_candidate_remaining_quantity_hidden",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "The delivery table contains a remaining-quantity column, but the row value was not visible or parsed.",
                    }
                )
            if self._inspection_row_has_hidden_decision(text, item):
                issues.append(
                    {
                        "code": "vl_candidate_inspection_decision_hidden",
                        "severity": "warn",
                        "line_index": index,
                        "item_name": item.get("item_name"),
                        "message": "The inspection table contains a decision column, but the row decision was not visible or parsed.",
                    }
                )

        if self._has_strong_return_credit_signal(text) and doc_type not in {
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
            "memo",
            "other",
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
        max_text_row_number = self._max_table_row_number(text)
        if total is not None and total > 0 and max_text_row_number is not None and max_text_row_number > total * Decimal("1.5"):
            issues.append(
                {
                    "code": "vl_candidate_total_row_amount_conflict",
                    "severity": "warn",
                    "actual_value": str(total),
                    "source_value": str(max_text_row_number),
                    "message": "VL output text contains table-row numeric values that conflict with the parsed document total.",
                }
            )
        return issues

    def _has_strong_return_credit_signal(self, text: str) -> bool:
        first_lines = "\n".join(line.strip() for line in str(text or "").splitlines()[:10])
        return bool(
            re.search(r"\b(?:RTN|RCM)[-_ ]?\d{4}", text, flags=re.IGNORECASE)
            or re.search(
                r"(반품\s*/?\s*(?:차감|크레딧)|크레딧\s*메모|반품\s*요청|차감\s*요청|반품전표|차감전표|"
                r"credit\s+(?:note|memo)|return\s+note|deduction)",
                first_lines,
                flags=re.IGNORECASE,
            )
        )

    def _text_has_total_label(self, text: str) -> bool:
        return bool(re.search(r"(총액|합계\\s*금액|합계금액|total\\s*(?:usd|amount)?|subtotal)", text or "", flags=re.IGNORECASE))

    def _max_table_row_number(self, text: str) -> Decimal | None:
        values: list[Decimal] = []
        in_table = False
        for line in str(text or "").splitlines():
            normalized = " ".join(line.split())
            if not normalized:
                continue
            if re.search(r"(품목명|반품품목|description|vendor\\s+sku|수량|qty)", normalized, flags=re.IGNORECASE):
                in_table = True
                continue
            if not in_table:
                continue
            if re.search(r"^(?:total|subtotal|tax|vat|총액|합계|공급가액|세액)", normalized, flags=re.IGNORECASE):
                break
            if not re.match(r"^(?:\d+\s+)?[A-Za-z가-힣0-9/()._-]+", normalized):
                continue
            if not re.search(r"[A-Za-z가-힣]", normalized):
                continue
            for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized):
                try:
                    value = Decimal(token.replace(",", ""))
                except Exception:
                    continue
                if value > 0:
                    values.append(value)
        return max(values) if values else None

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

    def _delivery_row_has_hidden_remaining_quantity(self, text: str, item: dict[str, Any]) -> bool:
        if not re.search(r"(잔량|remaining\s+qty|balance\s+qty)", text or "", flags=re.IGNORECASE):
            return False
        has_ordered = item.get("ordered_quantity") not in (None, "", [])
        has_delivered = item.get("delivered_quantity") not in (None, "", [])
        has_remaining = item.get("remaining_quantity") not in (None, "", [])
        return has_ordered and has_delivered and not has_remaining

    def _inspection_row_has_hidden_decision(self, text: str, item: dict[str, Any]) -> bool:
        if not re.search(r"(판정|inspection\s+result|decision)", text or "", flags=re.IGNORECASE):
            return False
        has_breakdown = any(
            item.get(field) not in (None, "", [])
            for field in ("received_quantity", "accepted_quantity", "rejected_quantity")
        )
        has_decision = any(
            item.get(field) not in (None, "", [])
            for field in ("decision", "inspection_result", "judgement", "result")
        )
        return has_breakdown and not has_decision

    def _row_boundary_needs_fax_review(self, text: str) -> bool:
        return bool(
            re.search(r"(\bfax\b|팩스|O/0|0/O|row\s*boundary|row boundary|행\s*경계)", text or "", flags=re.IGNORECASE)
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
                "message": "VL output contains low-confidence or distorted-source signals; require review before business-data export.",
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

    def _safe_nonnegative_amount(self, value: Any) -> Any:
        numeric_value = self._decimal_value(value)
        if numeric_value is not None and numeric_value < 0:
            return None
        return self._safe_value(value)
