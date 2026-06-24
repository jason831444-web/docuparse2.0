from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.document import Document


VL_KEY_VALUE_SOURCE = "vl_key_value"
VL_RAW_TEXT_KEY_VALUE_SOURCE = "vl_raw_text_key_value"
OCR_KEY_VALUE_SOURCE = "ocr_key_value"
RAW_TEXT_KEY_VALUE_SOURCE = "raw_text_key_value"


class RawExtractionSnapshotService:
    """Builds the review-first view from raw extraction artifacts.

    The snapshot is intentionally close to what OCR/VL returned: key-value pairs
    and table cells are preserved before they are mapped into business meaning.
    """

    def build(
        self,
        document: Document,
        *,
        source: str = "review_snapshot",
        reviewed_key_values: list[dict[str, Any]] | None = None,
        line_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
        key_values: list[dict[str, Any]] = []
        raw_tables = self._raw_tables(metadata)
        reviewed_tables = self._reviewed_line_item_tables(document)
        tables = reviewed_tables or raw_tables if source in {"manual_update", "confirmed_review"} else raw_tables or reviewed_tables
        existing_key_values = self._existing_key_values(metadata)
        if reviewed_key_values is not None:
            key_values = self._reviewed_key_values(existing_key_values, reviewed_key_values)
        elif existing_key_values and source in {"manual_update", "confirmed_review"}:
            key_values = existing_key_values
        else:
            self._add_raw_text_key_values(document.raw_text, key_values, source=self._raw_text_key_value_source(document))
        key_values = self._plain_key_values(self._dedupe_key_values(key_values))

        return {
            "version": "raw_extraction_v1",
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key_values": key_values,
            "tables": tables,
        }

    def _add_ocr_line_key_values(self, line_candidates: list[dict[str, Any]], key_values: list[dict[str, Any]]) -> None:
        if not line_candidates:
            return
        scale_x = max([self._candidate_coord(candidate, "x_max") or 0 for candidate in line_candidates] + [1.0])
        scale_y = max([self._candidate_coord(candidate, "y_max") or 0 for candidate in line_candidates] + [1.0])
        self._add_ocr_row_key_values(line_candidates, key_values, scale_x=scale_x, scale_y=scale_y)
        section: str | None = None
        for candidate in line_candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text") or "").strip()
            section = self._key_value_section_from_line(text) or section
            parsed_items = self._parse_key_value_line(text)
            if not parsed_items:
                continue
            bbox = self._line_candidate_bbox(candidate, scale_x=scale_x, scale_y=scale_y)
            for key, value, start, end in parsed_items:
                full_key = self._sectioned_key(section, key)
                if self._has_existing_key_value_key(key_values, full_key):
                    continue
                item_bbox = self._slice_bbox_by_text_span(text, bbox, start, end)
                key_bbox, value_bbox = self._split_key_value_bbox(text[start:end], key, item_bbox)
                self._append_key_value(
                    key_values,
                    full_key,
                    value,
                    OCR_KEY_VALUE_SOURCE,
                    confidence=candidate.get("confidence"),
                )

    def _add_ocr_row_key_values(
        self,
        line_candidates: list[dict[str, Any]],
        key_values: list[dict[str, Any]],
        *,
        scale_x: float,
        scale_y: float,
    ) -> None:
        section: str | None = None
        for row in self._ocr_line_candidate_rows(line_candidates):
            section = self._row_section_hint(row) or section
            for parsed in self._parse_ocr_candidate_row(row, scale_x=scale_x, scale_y=scale_y):
                key, value, item_bbox, key_bbox, value_bbox, confidence, page_index = parsed
                full_key = self._sectioned_key(section, key)
                if self._has_existing_key_value_key(key_values, full_key):
                    continue
                self._append_key_value(
                    key_values,
                    full_key,
                    value,
                    OCR_KEY_VALUE_SOURCE,
                    confidence=confidence,
                )

    def _parse_ocr_candidate_row(
        self,
        row: list[dict[str, Any]],
        *,
        scale_x: float,
        scale_y: float,
    ) -> list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None, float | None, object]]:
        tokens = [item for item in row if str(item.get("text") or "").strip()]
        parsed: list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None, float | None, object]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            text = str(token.get("text") or "").strip()
            next_index = index + 1
            key: str | None = None
            value_parts: list[str] = []
            key_candidates = [token]
            value_candidates: list[dict[str, Any]] = []

            split = re.match(r"^\s*([^:：]{1,30})\s*[:：]\s*(.*)$", text)
            if split and self._is_known_raw_key(split.group(1)):
                if re.search(rf"\s(?:{self._known_key_label_pattern()})\s*[:：]", split.group(2), flags=re.IGNORECASE):
                    index += 1
                    continue
                key = self._clean_raw_key(split.group(1))
                if split.group(2).strip():
                    value_parts.append(split.group(2).strip())
                    value_candidates.append(token)
            elif self._is_known_raw_key(text):
                key = self._clean_raw_key(text)
            elif self._looks_like_expected_total_key(tokens, index):
                key = "예상 합계"
                key_candidates = tokens[index : index + 2]
                next_index = index + 2

            if not key:
                index += 1
                continue

            while next_index < len(tokens):
                next_token = tokens[next_index]
                next_text = str(next_token.get("text") or "").strip()
                if not next_text:
                    next_index += 1
                    continue
                if self._key_value_section_from_line(next_text) or self._is_known_raw_key_token(next_text):
                    break
                if value_candidates and self._candidate_gap(value_candidates[-1], next_token) > 90:
                    break
                if not value_candidates and self._candidate_gap(key_candidates[-1], next_token) > 90:
                    break
                if not self._should_attach_value_token(key, value_parts, next_text):
                    break
                value_parts.append(next_text)
                value_candidates.append(next_token)
                next_index += 1

            value = self._clean_raw_value(" ".join(value_parts))
            if self._is_valid_raw_key_value(key, value):
                item_candidates = key_candidates + value_candidates
                item_bbox = self._row_candidate_bbox(item_candidates, scale_x=scale_x, scale_y=scale_y)
                key_bbox = self._row_candidate_bbox(key_candidates, scale_x=scale_x, scale_y=scale_y)
                value_bbox = self._row_candidate_bbox(value_candidates, scale_x=scale_x, scale_y=scale_y)
                confidence = self._average_candidate_confidence(item_candidates)
                page_index = next((item.get("page_index") or item.get("page") for item in item_candidates if item.get("page_index") or item.get("page")), None)
                parsed.append((key, value, item_bbox, key_bbox, value_bbox, confidence, page_index))
            index = max(next_index, index + 1)
        return parsed

    def _parse_key_value_line(self, text: str) -> list[tuple[str, str, int, int]]:
        if not text or len(text) > 160:
            return []
        known_label_pattern = self._known_key_label_pattern()
        colon_matches = list(re.finditer(rf"(?:(?<=^)|(?<=\s))({known_label_pattern})\s*[:：]\s*", text, flags=re.IGNORECASE))
        if not colon_matches:
            colon_matches = list(re.finditer(r"^\s*([^:：\n]{1,40})\s*[:：]\s*", text))
        if colon_matches:
            items: list[tuple[str, str, int, int]] = []
            for index, match in enumerate(colon_matches):
                value_start = match.end()
                value_end = colon_matches[index + 1].start() if index + 1 < len(colon_matches) else len(text)
                key = self._clean_raw_key(match.group(1))
                value = self._clean_raw_value(text[value_start:value_end])
                if self._is_valid_raw_key_value(key, value):
                    items.append((key, value, match.start(), value_end))
            return items
        match = re.match(rf"^\s*({known_label_pattern})\s+(.{{1,80}})\s*$", text, flags=re.IGNORECASE)
        if not match:
            match = re.match(r"^\s*([가-힣A-Za-z0-9/().\s]{1,30})\s{2,}(.{1,80})\s*$", text)
        if not match:
            match = re.match(r"^\s*(예상\s*합계|합계\s*금액|총\s*합계|크레[딧뒷]\s*합계|반품\s*합계|조정\s*합계|차감\s*합계|TOTAL(?:\s+[A-Z]+)?)\s+(-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*$", text, flags=re.IGNORECASE)
        if not match:
            match = self._glued_key_value_match(text)
        if not match:
            return []
        key = self._clean_raw_key(match.group(1))
        value = self._clean_raw_value(match.group(2))
        return [(key, value, match.start(1), match.end(2))] if self._is_valid_raw_key_value(key, value) else []

    def _known_key_label_pattern(self) -> str:
        return (
            r"문서\s*번호|문시\s*빈호|운서\s*번호|샘플\s*번호|팸플\s*번호|생플\s*번호|생플\s*변호|생품\s*번호|생표\s*변호|생물\s*변호|참조\s*번호|관련\s*문서\s*번호|원\s*문서|"
            r"사업자\s*번호|사엽자\s*변호|작성일|작성임|발행일|견적일|유효\s*기간|"
            r"납기일|요청일|일자|매장|담당|당당|상호|공급자|공급받는자|입고창고|출고창고|요청부서|"
            r"예상\s*합계|총\s*합계|합계\s*금액|합계|크레[딧뒷]\s*합계|반품\s*합계|조정\s*합계|차감\s*합계|"
            r"실판매\s*금액|순판매\s*금액|과세\s*합계|공급\s*가액|"
            r"부가\s*세|세액|V\.?\s*A\.?\s*T|VAT|"
            r"결제\s*합계|현금\s*합계|카드\s*합계|온라인\s*결제|주문\s*횟수|매장\s*판매|매장\s*판애|"
            r"배달\s*판매|배달\s*판마|평균\s*단가"
        )

    def _clean_raw_key(self, value: str) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        value = re.sub(r"^(?:[-*•·]+\s*)+", "", value).strip()
        aliases = {
            "팸플번호": "샘플번호",
            "생플번호": "샘플번호",
            "생플변호": "샘플번호",
            "생품번호": "샘플번호",
            "생표변호": "샘플번호",
            "생물변호": "샘플번호",
            "샘플 번호": "샘플번호",
            "문서 번호": "문서번호",
            "문시빈호": "문서번호",
            "운서번호": "문서번호",
            "참조 번호": "참조번호",
            "관련 문서 번호": "관련문서번호",
            "원 문서": "원문서",
            "사엽자변호": "사업자번호",
            "작성임": "작성일",
            "사업자 번호": "사업자번호",
            "당당": "담당",
            "실판매 금액": "실판매금액",
            "순판매 금액": "순판매금액",
            "과세 합계": "과세합계",
            "공급 가액": "공급가액",
            "부가 세": "부가세",
            "총 합계": "총합계",
            "합계 금액": "합계금액",
            "크레딧 합계": "크레딧합계",
            "크레뒷 합계": "크레딧합계",
            "크레뒷합계": "크레딧합계",
            "반품 합계": "반품합계",
            "조정 합계": "조정합계",
            "차감 합계": "차감합계",
            "결제 합계": "결제합계",
            "현금 합계": "현금합계",
            "카드 합계": "카드합계",
            "온라인 결제": "온라인결제",
            "주문 횟수": "주문횟수",
            "매장 판매": "매장판매",
            "매장판애": "매장판매",
            "배달 판매": "배달판매",
            "배달판마": "배달판매",
            "평균 단가": "평균단가",
            "V.A.T": "VAT",
            "V A T": "VAT",
        }
        compact = re.sub(r"\s+", "", value)
        return aliases.get(value) or aliases.get(compact) or value

    def _clean_raw_value(self, value: str) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        value = re.sub(r"([A-Z]{2,})\s*-\s*([0-9])", r"\1-\2", value)
        return value.strip(" |")

    def _is_valid_raw_key_value(self, key: str, value: str) -> bool:
        if not key or not value:
            return False
        if len(key) > 40 or len(value) > 100:
            return False
        if re.fullmatch(r"[-_./\\|]+", key) or re.fullmatch(r"[-_./\\|]+", value):
            return False
        if key.casefold() in {"document_type", "document_number", "currency", "title"}:
            return False
        if key in {"문서유형", "제목", "통화"}:
            return False
        return True

    def _key_value_section_from_line(self, text: str) -> str | None:
        normalized = re.sub(r"\s+", "", str(text or ""))
        if normalized in {"공급자", "공급처", "공급지"}:
            return "공급자"
        if normalized in {"공급받는자", "공급받는자정보", "공급반는지", "고객사"}:
            return "공급받는자"
        return None

    def _trailing_section_from_value(self, value: str) -> tuple[str, str | None]:
        text = str(value or "").strip()
        for marker, section in (
            ("공급받는자", "공급받는자"),
            ("공급받는자 정보", "공급받는자"),
            ("고객사", "공급받는자"),
            ("공급자", "공급자"),
            ("공급처", "공급자"),
        ):
            if text.endswith(marker):
                cleaned = text[: -len(marker)].strip()
                if cleaned:
                    return cleaned, section
        return text, None

    def _sectioned_key(self, section: str | None, key: str) -> str:
        key = str(key or "").strip()
        if section and key in {"상호", "사업자번호", "담당", "대표자", "주소"}:
            return f"{section} {key}"
        return key

    def _is_known_raw_key(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", str(text or "")).strip(":：")
        known = {
            "문서번호",
            "샘플번호",
            "팸플번호",
            "생플번호",
            "생플변호",
            "생품번호",
            "생표변호",
            "생물변호",
            "참조번호",
            "관련문서번호",
            "원문서",
            "문시빈호",
            "운서번호",
            "사업자번호",
            "사엽자변호",
            "작성일",
            "작성임",
            "발행일",
            "견적일",
            "유효기간",
            "납기일",
            "요청일",
            "일자",
            "매장",
            "담당",
            "당당",
            "상호",
            "입고창고",
            "출고창고",
            "요청부서",
            "예상합계",
            "총합계",
            "합계금액",
            "합계",
            "크레딧합계",
            "크레뒷합계",
            "반품합계",
            "조정합계",
            "차감합계",
            "실판매금액",
            "순판매금액",
            "과세합계",
            "공급가액",
            "부가세",
            "세액",
            "VAT",
            "V.A.T",
            "결제합계",
            "현금합계",
            "카드합계",
            "온라인결제",
            "주문횟수",
            "매장판매",
            "매장판애",
            "배달판매",
            "배달판마",
            "평균단가",
        }
        return normalized in known

    def _is_known_raw_key_token(self, text: str) -> bool:
        stripped = str(text or "").strip()
        split = re.match(r"^\s*([^:：]{1,30})\s*[:：]", stripped)
        return self._is_known_raw_key(split.group(1) if split else stripped)

    def _looks_like_expected_total_key(self, tokens: list[dict[str, Any]], index: int) -> bool:
        current = re.sub(r"\s+", "", str(tokens[index].get("text") or ""))
        next_text = re.sub(r"\s+", "", str(tokens[index + 1].get("text") or "")) if index + 1 < len(tokens) else ""
        return current == "예상" and next_text == "합계"

    def _should_attach_value_token(self, key: str, value_parts: list[str], text: str) -> bool:
        normalized_key = re.sub(r"\s+", "", key)
        normalized_text = str(text or "").strip()
        if normalized_key == "문서번호":
            candidate = self._clean_raw_value(" ".join([*value_parts, normalized_text]))
            return bool(re.fullmatch(r"[A-Za-z]{1,10}(?:-\d{1,10})?|-?\d{1,10}", candidate))
        if normalized_key in {"작성일", "발행일", "견적일", "납기일", "요청일", "일자"}:
            return not value_parts and bool(re.fullmatch(r"\d{4}[./-]?\d{2}[./-]?\d{2}", normalized_text))
        if normalized_key in {"샘플번호", "팸플번호"}:
            return not value_parts and bool(re.fullmatch(r"[A-Za-z0-9-]{1,20}", normalized_text))
        if normalized_key in {"사업자번호", "사엽자변호"}:
            return not value_parts and bool(re.fullmatch(r"\d{3}-?\d{2}-?\d{5}", normalized_text))
        if normalized_key == "예상합계":
            return not value_parts and bool(re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", normalized_text))
        if normalized_key in {
            "실판매금액",
            "순판매금액",
            "과세합계",
            "공급가액",
            "VAT",
            "결제합계",
            "현금합계",
            "카드합계",
            "온라인결제",
            "주문횟수",
            "매장판매",
            "배달판매",
            "평균단가",
        }:
            return not value_parts and bool(re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", normalized_text))
        if len(value_parts) >= 3:
            return False
        return True

    def _glued_key_value_match(self, text: str) -> re.Match[str] | None:
        labels = (
            "문서번호",
            "샘플번호",
            "생플번호",
            "생플변호",
            "참조번호",
            "관련문서번호",
            "원문서",
            "작성일",
            "발행일",
            "견적일",
            "납기일",
            "요청일",
            "일자",
            "상호",
            "담당",
            "합계금액",
            "총합계",
            "크레딧합계",
            "크레뒷합계",
            "반품합계",
            "조정합계",
            "차감합계",
        )
        pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
        return re.match(rf"^\s*({pattern})\s*(?![:：])(.{{2,80}})\s*$", text, flags=re.IGNORECASE)

    def _add_raw_text_key_values(self, raw_text: object, key_values: list[dict[str, Any]], *, source: str = RAW_TEXT_KEY_VALUE_SOURCE) -> None:
        lines = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
        section: str | None = None
        pending_sections: list[str] = []
        for index, raw_line in enumerate(lines):
            line = self._raw_text_line_with_continuation(lines, index)
            line_section = self._key_value_section_from_line(line)
            if line_section:
                section = line_section
                pending_sections.append(line_section)
            for key, value, _start, _end in self._parse_key_value_line(line):
                value, next_section = self._trailing_section_from_value(value)
                target_section = section
                if key in {"상호", "사업자번호", "담당", "대표자", "주소"} and pending_sections and not re.search(r"[:：]", line):
                    target_section = pending_sections.pop(0)
                full_key = self._sectioned_key(target_section, key)
                if self._has_existing_key_value_key(key_values, full_key):
                    self._replace_existing_key_value_if_better(key_values, full_key, value, source, section=target_section if full_key != key else None)
                    if next_section:
                        section = next_section
                        pending_sections.append(next_section)
                    continue
                self._append_key_value(
                    key_values,
                    full_key,
                    value,
                    source,
                    section=target_section if full_key != key else None,
                )
                if next_section:
                    section = next_section
                    pending_sections.append(next_section)
        self._add_raw_text_split_line_key_values(lines, key_values, source=source)

    def _raw_text_key_value_source(self, document: Document) -> str:
        method = str(getattr(document, "extraction_method", "") or "").casefold()
        metadata = document.workflow_metadata if isinstance(document.workflow_metadata, dict) else {}
        vl_metadata = metadata.get("vl_provider_metadata") if isinstance(metadata.get("vl_provider_metadata"), dict) else {}
        if "vl" in method or "paddle" in method or vl_metadata:
            return VL_RAW_TEXT_KEY_VALUE_SOURCE
        return RAW_TEXT_KEY_VALUE_SOURCE

    def _add_raw_text_split_line_key_values(self, lines: list[str], key_values: list[dict[str, Any]], *, source: str) -> None:
        section: str | None = None
        pending_sections: list[str] = []
        for index, line in enumerate(lines):
            line_section = self._key_value_section_from_line(line)
            if line_section:
                section = line_section
                pending_sections.append(line_section)
                continue
            key = self._raw_text_split_line_key(line)
            if not key:
                continue
            value = self._raw_text_split_line_value(lines, index + 1)
            if not value or not self._is_valid_raw_key_value(key, value):
                continue
            target_section = section
            if key in {"상호", "사업자번호", "담당", "대표자", "주소"} and pending_sections:
                target_section = pending_sections.pop(0)
            full_key = self._sectioned_key(target_section, key)
            if self._has_existing_key_value_key(key_values, full_key):
                self._replace_existing_key_value_if_better(key_values, full_key, value, source, section=target_section if full_key != key else None)
                continue
            self._append_key_value(
                key_values,
                full_key,
                value,
                source,
                section=target_section if full_key != key else None,
            )

    def _raw_text_line_with_continuation(self, lines: list[str], index: int) -> str:
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if re.match(r"^\s*(문서\s*번호|샘플\s*번호|팸플\s*번호)\s*[:：]\s*[A-Za-z]{1,8}\s*$", line, flags=re.IGNORECASE) and re.match(
            r"^\s*[-_/]?\s*[A-Za-z0-9]{1,12}\s*$",
            next_line,
        ):
            return f"{line}{next_line.strip()}"
        return line

    def _raw_text_split_line_key(self, line: str) -> str | None:
        split = re.match(r"^\s*([^:：]{1,30})\s*[:：]\s*$", line)
        candidate = split.group(1) if split else line
        return self._clean_raw_key(candidate) if self._is_known_raw_key(candidate) else None

    def _raw_text_split_line_value(self, lines: list[str], start_index: int) -> str | None:
        for next_line in lines[start_index : start_index + 3]:
            if self._key_value_section_from_line(next_line) or self._raw_text_split_line_key(next_line):
                return None
            if self._parse_key_value_line(next_line):
                return None
            value = self._clean_raw_value(next_line)
            if value:
                return value
        return None

    def _add_vl_direct_key_values(self, metadata: dict[str, Any], key_values: list[dict[str, Any]]) -> None:
        for candidate in self._vl_candidates(metadata):
            structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else {}
            for item in self._direct_key_value_items(candidate, structured):
                key = item.get("key") or item.get("label") or item.get("field") or item.get("name")
                value = item.get("value") if item.get("value") is not None else item.get("normalized_value")
                self._append_key_value(
                    key_values,
                    key,
                    value,
                    VL_KEY_VALUE_SOURCE,
                    role=str(item.get("role") or item.get("field") or "") or None,
                    confidence=item.get("confidence"),
                    section=str(item.get("section") or item.get("group") or "") or None,
                )

    def _raw_tables(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in self._vl_candidates(metadata):
            candidate_tables = list(candidate.get("tables") or [])
            structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else {}
            candidate_tables.extend(structured.get("tables") or [])
            for table in candidate_tables:
                if not isinstance(table, dict):
                    continue
                raw_rows = self._table_rows(table)
                if not raw_rows:
                    continue
                columns = self._table_columns(table, raw_rows)
                key = repr((table.get("table_type"), columns, raw_rows[:3]))
                if key in seen:
                    continue
                seen.add(key)
                table_record = {
                    "table_type": table.get("table_type") or "table",
                    "source": table.get("source") or "unknown",
                    "columns": columns,
                    "rows": raw_rows,
                    "row_count": len(raw_rows),
                }
                raw_columns = table.get("raw_columns") if isinstance(table.get("raw_columns"), list) else []
                source_raw_rows = table.get("raw_rows") if isinstance(table.get("raw_rows"), list) else []
                if raw_columns:
                    table_record["raw_columns"] = [self._json_value(value) for value in raw_columns]
                if source_raw_rows:
                    table_record["raw_rows"] = [self._json_value(value) for value in source_raw_rows]
                tables.append(table_record)
        return tables

    def _reviewed_line_item_tables(self, document: Document) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        columns: list[str] = []
        labels = {
            "line_number": "No",
            "item_name": "품목명",
            "document_item_code": "품목코드",
            "item_code": "품목코드",
            "specification": "규격",
            "lot_code": "Lot/Code",
            "quantity": "수량",
            "unit": "단위",
            "unit_price": "단가",
            "supply_amount": "공급가액",
            "tax_amount": "세액",
            "line_total": "금액",
            "inspection_result": "판정",
            "inspection_item": "검사항목",
            "note": "비고",
        }
        for item in document.line_items or []:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {}
            for key, value in item.items():
                if key.startswith("_") or key in {"item_master_candidates"} or value in (None, ""):
                    continue
                label = labels.get(str(key), str(key))
                row[label] = self._json_value(value)
                if label not in columns:
                    columns.append(label)
            if row:
                rows.append(row)
        if not rows:
            return []
        return [{
            "table_type": "reviewed_line_items",
            "source": "user_reviewed_line_items",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }]

    def _table_rows(self, table: dict[str, Any]) -> list[dict[str, Any]]:
        rows = table.get("rows") if isinstance(table.get("rows"), list) else []
        raw_rows = table.get("raw_rows") if isinstance(table.get("raw_rows"), list) else []
        raw_columns = table.get("raw_columns") if isinstance(table.get("raw_columns"), list) else []
        columns = table.get("columns") if isinstance(table.get("columns"), list) else []
        source_columns = [str(column) for column in raw_columns or columns if str(column).strip()]
        normalized: list[dict[str, Any]] = []
        if raw_rows:
            for row in raw_rows:
                if isinstance(row, dict):
                    normalized.append({str(key): self._json_value(value) for key, value in row.items()})
                elif isinstance(row, list):
                    normalized.append({
                        source_columns[index] if index < len(source_columns) else str(index + 1): self._json_value(value)
                        for index, value in enumerate(row)
                    })
        if normalized:
            return normalized
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_cells = row.get("raw_cells") if isinstance(row.get("raw_cells"), dict) else None
            source = raw_cells or {key: value for key, value in row.items() if not str(key).startswith("_")}
            normalized.append({str(key): self._json_value(value) for key, value in source.items()})
        return normalized

    def _table_columns(self, table: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
        raw_columns = table.get("raw_columns") if isinstance(table.get("raw_columns"), list) else []
        columns = table.get("columns") if isinstance(table.get("columns"), list) else []
        selected = [str(column) for column in raw_columns or columns if str(column).strip()]
        if selected:
            return selected
        keys: list[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        return keys

    def _vl_candidates(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = metadata.get("vl_candidates")
        return [item for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []

    def _direct_key_value_items(self, *containers: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for container in containers:
            for key in ("key_values", "raw_key_values", "document_key_values"):
                values = container.get(key)
                if isinstance(values, list):
                    items.extend(item for item in values if isinstance(item, dict))
        return items

    def _existing_key_values(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        raw = metadata.get("raw_extraction") if isinstance(metadata.get("raw_extraction"), dict) else {}
        values = raw.get("key_values") if isinstance(raw.get("key_values"), list) else []
        return [dict(item) for item in values if isinstance(item, dict) and item.get("key") is not None]

    def _reviewed_key_values(self, existing: list[dict[str, Any]], reviewed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not existing:
            return []
        reviewed_by_id = {self._reviewed_key_value_identity(item): item for item in reviewed if isinstance(item, dict)}
        result: list[dict[str, Any]] = []
        for item in existing:
            identity = self._key_value_identity(item)
            replacement = reviewed_by_id.get(identity)
            if replacement is None:
                result.append(dict(item))
                continue
            next_item = dict(item)
            replacement_key = replacement.get("key")
            if replacement_key not in (None, ""):
                next_item["key"] = str(replacement_key)
            next_item["value"] = self._json_value(replacement.get("value"))
            next_item["reviewed"] = True
            result.append(next_item)
        return result

    def _reviewed_key_value_identity(self, item: dict[str, Any]) -> str:
        explicit = item.get("_review_identity") or item.get("review_identity")
        return str(explicit) if explicit not in (None, "") else self._key_value_identity(item)

    def _key_value_identity(self, item: dict[str, Any]) -> str:
        return "|".join(str(item.get(key) or "") for key in ("key", "source", "role", "section"))

    def _has_existing_key_value_key(self, key_values: list[dict[str, Any]], key: str, *, source: str | None = None) -> bool:
        normalized = re.sub(r"\s+", " ", str(key or "")).strip().casefold()
        for item in key_values:
            if source and item.get("source") != source:
                continue
            item_key = re.sub(r"\s+", " ", str(item.get("key") or "")).strip().casefold()
            if item_key == normalized:
                return True
        return False

    def _append_key_value(
        self,
        key_values: list[dict[str, Any]],
        key: object,
        value: object,
        source: str,
        *,
        role: str | None = None,
        confidence: object = None,
        section: str | None = None,
        bbox: object = None,
        page_index: object = None,
        key_bbox: object = None,
        value_bbox: object = None,
        bbox_source: str | None = None,
    ) -> None:
        if key is None or value in (None, ""):
            return
        item: dict[str, Any] = {"key": str(key), "value": self._json_value(value), "source": source}
        if role:
            item["role"] = role
        if section:
            item["section"] = section
        if confidence not in (None, ""):
            item["confidence"] = self._json_value(confidence)
        normalized_bbox = self._normalize_bbox(bbox)
        normalized_key_bbox = self._normalize_bbox(key_bbox)
        normalized_value_bbox = self._normalize_bbox(value_bbox)
        key_values.append(item)

    def _replace_existing_key_value_if_better(
        self,
        key_values: list[dict[str, Any]],
        key: str,
        value: object,
        source: str,
        *,
        section: str | None = None,
    ) -> None:
        if value in (None, ""):
            return
        normalized = re.sub(r"\s+", " ", str(key or "")).strip().casefold()
        new_value = self._json_value(value)
        for item in key_values:
            item_key = re.sub(r"\s+", " ", str(item.get("key") or "")).strip().casefold()
            if item_key != normalized:
                continue
            old_value = item.get("value")
            if self._key_value_replacement_score(key, new_value) >= self._key_value_replacement_score(key, old_value):
                item["value"] = new_value
                item["source"] = source
                if section:
                    item["section"] = section
            return

    def _key_value_replacement_score(self, key: object, value: object) -> int:
        text = str(value or "").strip()
        if not text:
            return -100
        score = min(len(text), 40)
        normalized_key = re.sub(r"\s+", "", str(key or ""))
        if normalized_key.endswith("상호"):
            score += 5
            if "(주)" in text or text.startswith("주"):
                score += 4
            if re.search(r"(사업자|담당|번호|작성일|품목|합계)", text):
                score -= 10
        if re.search(r"[A-Za-z가-힣]", text) and re.search(r"\d", text):
            score -= 2
        return score

    def _candidate_coord(self, candidate: dict[str, Any], key: str) -> float | None:
        try:
            value = candidate.get(key)
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _line_candidate_bbox(self, candidate: dict[str, Any], *, scale_x: float, scale_y: float) -> list[float] | None:
        values = [self._candidate_coord(candidate, key) for key in ("x_min", "y_min", "x_max", "y_max")]
        if any(value is None for value in values):
            bbox = candidate.get("bbox")
            if isinstance(bbox, list) and bbox:
                try:
                    xs = [float(point[0]) for point in bbox if isinstance(point, (list, tuple)) and len(point) >= 2]
                    ys = [float(point[1]) for point in bbox if isinstance(point, (list, tuple)) and len(point) >= 2]
                except (TypeError, ValueError):
                    return None
                if not xs or not ys:
                    return None
                values = [min(xs), min(ys), max(xs), max(ys)]
            else:
                return None
        x1, y1, x2, y2 = [float(value or 0) for value in values]
        divisor_x = scale_x if scale_x > 1 else 1.0
        divisor_y = scale_y if scale_y > 1 else 1.0
        return [
            max(0.0, min(1.0, x1 / divisor_x)),
            max(0.0, min(1.0, y1 / divisor_y)),
            max(0.0, min(1.0, x2 / divisor_x)),
            max(0.0, min(1.0, y2 / divisor_y)),
        ]

    def _ocr_line_candidate_rows(self, candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        positioned = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and self._candidate_coord(candidate, "y_min") is not None
            and self._candidate_coord(candidate, "y_max") is not None
        ]
        positioned.sort(key=lambda item: ((self._candidate_coord(item, "y_min") or 0) + (self._candidate_coord(item, "y_max") or 0)) / 2)
        rows: list[list[dict[str, Any]]] = []
        for candidate in positioned:
            center = ((self._candidate_coord(candidate, "y_min") or 0) + (self._candidate_coord(candidate, "y_max") or 0)) / 2
            target = None
            for row in rows:
                row_center = sum(((self._candidate_coord(item, "y_min") or 0) + (self._candidate_coord(item, "y_max") or 0)) / 2 for item in row) / len(row)
                if abs(row_center - center) <= 8:
                    target = row
                    break
            if target is None:
                rows.append([candidate])
            else:
                target.append(candidate)
        for row in rows:
            row.sort(key=lambda item: self._candidate_coord(item, "x_min") or 0)
        return rows

    def _row_candidate_bbox(self, row: list[dict[str, Any]], *, scale_x: float, scale_y: float) -> list[float] | None:
        bboxes = [self._line_candidate_bbox(item, scale_x=scale_x, scale_y=scale_y) for item in row]
        bboxes = [bbox for bbox in bboxes if bbox]
        if not bboxes:
            return None
        return [
            min(bbox[0] for bbox in bboxes),
            min(bbox[1] for bbox in bboxes),
            max(bbox[2] for bbox in bboxes),
            max(bbox[3] for bbox in bboxes),
        ]

    def _row_section_hint(self, row: list[dict[str, Any]]) -> str | None:
        for item in row:
            section = self._key_value_section_from_line(str(item.get("text") or ""))
            if section:
                return section
        return None

    def _candidate_gap(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        left_x = self._candidate_coord(left, "x_max")
        right_x = self._candidate_coord(right, "x_min")
        if left_x is None or right_x is None:
            return 0.0
        return right_x - left_x

    def _average_candidate_confidence(self, candidates: list[dict[str, Any]]) -> float | None:
        confidence_numbers = [value for value in (self._numeric_value(item.get("confidence")) for item in candidates) if value is not None]
        return round(sum(confidence_numbers) / len(confidence_numbers), 4) if confidence_numbers else None

    def _numeric_value(self, value: object) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _split_key_value_bbox(self, text: str, key: str, bbox: list[float] | None) -> tuple[list[float] | None, list[float] | None]:
        if not bbox:
            return None, None
        separator_index = max(text.find(":"), text.find("："))
        if separator_index < 0:
            separator_index = len(key)
        denominator = max(len(text), 1)
        split = bbox[0] + (bbox[2] - bbox[0]) * min(0.85, max(0.15, (separator_index + 1) / denominator))
        key_bbox = [bbox[0], bbox[1], split, bbox[3]]
        value_bbox = [split, bbox[1], bbox[2], bbox[3]]
        return key_bbox, value_bbox

    def _slice_bbox_by_text_span(self, text: str, bbox: list[float] | None, start: int, end: int) -> list[float] | None:
        if not bbox:
            return None
        length = max(len(text), 1)
        span_start = max(0.0, min(1.0, start / length))
        span_end = max(span_start, min(1.0, end / length))
        width = bbox[2] - bbox[0]
        return [
            bbox[0] + width * span_start,
            bbox[1],
            bbox[0] + width * span_end,
            bbox[3],
        ]

    def _bbox_from_item(self, item: dict[str, Any]) -> object | None:
        for key in ("normalized_bbox", "bbox", "box", "bounding_box", "bbox_span"):
            value = item.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _page_index_from_item(self, item: dict[str, Any]) -> object | None:
        for key in ("page_index", "page", "page_no"):
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None

    def _normalize_bbox(self, bbox: object) -> list[float] | None:
        if isinstance(bbox, dict):
            values = [bbox.get(key) for key in ("x1", "y1", "x2", "y2")]
        elif isinstance(bbox, (list, tuple)):
            values = list(bbox[:4])
        else:
            return None
        if len(values) != 4 or any(value is None for value in values):
            return None
        try:
            numbers = [float(value) for value in values]
        except (TypeError, ValueError):
            return None
        max_value = max(abs(value) for value in numbers)
        if max_value > 1:
            width = max(numbers[0], numbers[2], 1.0)
            height = max(numbers[1], numbers[3], 1.0)
            numbers = [numbers[0] / width, numbers[1] / height, numbers[2] / width, numbers[3] / height]
        return [max(0.0, min(1.0, value)) for value in numbers]

    def _dedupe_key_values(self, key_values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, Any]] = []
        for item in key_values:
            identity = (
                re.sub(r"\s+", " ", str(item.get("key") or "")).strip().casefold(),
                re.sub(r"\s+", " ", str(item.get("value") or "")).strip(),
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(item)
        return result

    def _plain_key_values(self, key_values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bbox_fields = {"bbox", "box", "bounding_box", "bbox_span", "normalized_bbox", "key_bbox", "value_bbox", "bbox_source", "page", "page_index", "page_no"}
        return [
            {key: value for key, value in item.items() if key not in bbox_fields}
            for item in key_values
        ]

    def _json_value(self, value: object) -> object:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value
