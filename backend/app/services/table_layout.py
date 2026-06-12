from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "line_no": ("no", "번호", "순번"),
    "item_name": ("품목명", "품명", "description", "item"),
    "document_item_code": ("문서품목코드", "vendor sku", "sku"),
    "internal_item_code": ("내부품목코드", "internal"),
    "specification": ("규격", "spec", "사양"),
    "quantity": ("수량", "qty", "quantity", "요청수량", "입고수량", "납품수량"),
    "unit": ("단위", "unit"),
    "unit_price": ("단가", "unit price"),
    "supply_amount": ("공급가액", "공급액", "subtotal", "amount"),
    "tax_amount": ("세액", "tax", "vat"),
    "line_total": ("합계", "합계금액", "total"),
}

FOOTER_TERMS = (
    "공급가액",
    "세액",
    "합계금액",
    "총액",
    "total amount",
    "grand total",
    "담당",
    "검토",
    "승인",
    "docuparse",
    "synthetic data",
    "페이지하단",
    "마지막페이지",
    "금액검토필요",
)

UNIT_TOKENS = {"EA", "SET", "PCS", "PC", "개", "대", "장"}


@dataclass
class LayoutToken:
    text: str
    confidence: float
    page: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    bbox: list[list[float]] = field(default_factory=list)

    @property
    def x_center(self) -> float:
        return (self.x_min + self.x_max) / 2

    @property
    def y_center(self) -> float:
        return (self.y_min + self.y_max) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y_max - self.y_min)


@dataclass
class LayoutRow:
    row_index: int
    page: int
    y_min: float
    y_max: float
    y_center: float
    text: str
    confidence: float
    tokens: list[LayoutToken]


@dataclass
class ColumnCandidate:
    name: str
    x_min: float
    x_max: float
    x_center: float
    confidence: float
    source: str
    source_text: str | None = None


@dataclass
class StructuredTableRow:
    row_index: int
    page: int
    fields: dict[str, Any]
    confidence: float
    missing_fields: list[str]
    untrusted_fields: list[str]
    review_flags: list[str]
    source_tokens: list[dict[str, Any]]
    bbox_span: dict[str, float] | None


class BBoxTableReconstructor:
    def group_rows_by_y(self, line_candidates: list[dict[str, Any]]) -> list[LayoutRow]:
        tokens = self._normalize_tokens(line_candidates)
        if not tokens:
            return []
        rows: list[dict[str, Any]] = []
        for token in sorted(tokens, key=lambda item: (item.page, item.y_center, item.x_center)):
            page_rows = [row for row in rows if row["page"] == token.page]
            tolerance = self._row_tolerance(page_rows, token)
            match = next((row for row in page_rows if abs(row["y_center"] - token.y_center) <= tolerance), None)
            if match is None:
                rows.append({"page": token.page, "y_center": token.y_center, "tokens": [token]})
                continue
            match["tokens"].append(token)
            match["y_center"] = sum(item.y_center for item in match["tokens"]) / len(match["tokens"])

        output: list[LayoutRow] = []
        for index, row in enumerate(sorted(rows, key=lambda item: (item["page"], item["y_center"])), start=1):
            row_tokens = sorted(row["tokens"], key=lambda item: item.x_center)
            y_min = min(item.y_min for item in row_tokens)
            y_max = max(item.y_max for item in row_tokens)
            output.append(LayoutRow(
                row_index=index,
                page=row["page"],
                y_min=y_min,
                y_max=y_max,
                y_center=sum(item.y_center for item in row_tokens) / len(row_tokens),
                text=" ".join(item.text for item in row_tokens if item.text).strip(),
                confidence=sum(item.confidence for item in row_tokens) / len(row_tokens),
                tokens=row_tokens,
            ))
        return output

    def infer_columns(self, rows: list[LayoutRow]) -> list[ColumnCandidate]:
        header = self._best_header_row(rows)
        if header:
            header_columns = self._columns_from_header(header)
            if len(header_columns) >= 2:
                return self._with_column_boundaries(header_columns)
        return self._columns_from_token_clusters(rows)

    def map_tokens_to_columns(
        self,
        rows: list[LayoutRow],
        columns: list[ColumnCandidate],
    ) -> list[StructuredTableRow]:
        if not rows:
            return []
        header_index = self._best_header_row(rows).row_index if self._best_header_row(rows) else 0
        structured: list[StructuredTableRow] = []
        for row in rows:
            if row.row_index <= header_index:
                continue
            if self._is_footer_or_note_row(row):
                continue
            mapped = self._map_row(row, columns)
            if not self._looks_like_item_or_candidate(row, mapped):
                continue
            structured.append(self._structured_row(row, mapped))
        return structured

    def build_line_item_candidates(
        self,
        table_rows: list[StructuredTableRow],
        document_profile: str | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        amount_required = document_profile not in {"no_price_document", "inventory_movement_document", "quality_document"}
        for row in table_rows:
            fields = dict(row.fields)
            flags = list(row.review_flags)
            if not fields.get("item_name"):
                flags.append("missing_item_name_from_ocr")
            if amount_required and any(key in fields for key in ("supply_amount", "tax_amount", "line_total")):
                flags.append("untrusted_ocr_amount")
            candidate = {
                **fields,
                "confidence": row.confidence,
                "missing_fields": sorted(set(row.missing_fields)),
                "untrusted_fields": sorted(set(row.untrusted_fields)),
                "review_flags": sorted(set(flags)),
                "source_tokens": row.source_tokens,
                "bbox_span": row.bbox_span,
            }
            candidates.append(candidate)
        return candidates

    def _normalize_tokens(self, line_candidates: list[dict[str, Any]]) -> list[LayoutToken]:
        tokens: list[LayoutToken] = []
        for candidate in line_candidates:
            text = str(candidate.get("text") or "").strip()
            if not text:
                continue
            rect = _rect_from_candidate(candidate)
            if rect is None:
                continue
            tokens.append(LayoutToken(
                text=text,
                confidence=_float(candidate.get("confidence"), default=0.0),
                page=int(candidate.get("page") or 1),
                x_min=rect["x_min"],
                y_min=rect["y_min"],
                x_max=rect["x_max"],
                y_max=rect["y_max"],
                bbox=candidate.get("bbox") if isinstance(candidate.get("bbox"), list) else [],
            ))
        return tokens

    def _row_tolerance(self, rows: list[dict[str, Any]], token: LayoutToken) -> float:
        heights = [item.height for row in rows for item in row["tokens"]]
        base = median(heights) if heights else token.height
        return max(8.0, min(30.0, base * 0.75))

    def _best_header_row(self, rows: list[LayoutRow]) -> LayoutRow | None:
        scored = [(self._header_score(row), row) for row in rows]
        scored = [(score, row) for score, row in scored if score >= 2]
        if not scored:
            return None
        return max(scored, key=lambda item: (item[0], -item[1].row_index))[1]

    def _header_score(self, row: LayoutRow) -> int:
        text = _compact(row.text)
        score = 0
        for aliases in HEADER_ALIASES.values():
            if any(_compact(alias) in text for alias in aliases):
                score += 1
        return score

    def _columns_from_header(self, row: LayoutRow) -> list[ColumnCandidate]:
        columns: list[ColumnCandidate] = []
        seen: set[str] = set()
        for token in row.tokens:
            normalized = _compact(token.text)
            for name, aliases in HEADER_ALIASES.items():
                if name in seen:
                    continue
                if any(_compact(alias) in normalized for alias in aliases):
                    seen.add(name)
                    columns.append(ColumnCandidate(
                        name=name,
                        x_min=token.x_min,
                        x_max=token.x_max,
                        x_center=token.x_center,
                        confidence=token.confidence,
                        source="header",
                        source_text=token.text,
                    ))
                    break
        return sorted(columns, key=lambda item: item.x_center)

    def _with_column_boundaries(self, columns: list[ColumnCandidate]) -> list[ColumnCandidate]:
        if not columns:
            return []
        sorted_columns = sorted(columns, key=lambda item: item.x_center)
        output: list[ColumnCandidate] = []
        for index, column in enumerate(sorted_columns):
            left = (sorted_columns[index - 1].x_center + column.x_center) / 2 if index else column.x_min - 40
            right = (column.x_center + sorted_columns[index + 1].x_center) / 2 if index + 1 < len(sorted_columns) else column.x_max + 80
            output.append(ColumnCandidate(
                name=column.name,
                x_min=left,
                x_max=right,
                x_center=column.x_center,
                confidence=column.confidence,
                source=column.source,
                source_text=column.source_text,
            ))
        return output

    def _columns_from_token_clusters(self, rows: list[LayoutRow]) -> list[ColumnCandidate]:
        centers = sorted(token.x_center for row in rows for token in row.tokens)
        clusters: list[list[float]] = []
        for x_center in centers:
            if not clusters or abs(clusters[-1][-1] - x_center) > 55:
                clusters.append([x_center])
            else:
                clusters[-1].append(x_center)
        columns: list[ColumnCandidate] = []
        for index, cluster in enumerate(clusters, start=1):
            if len(cluster) < 2:
                continue
            center = sum(cluster) / len(cluster)
            columns.append(ColumnCandidate(
                name=f"unknown_{index}",
                x_min=min(cluster) - 28,
                x_max=max(cluster) + 28,
                x_center=center,
                confidence=min(0.75, 0.35 + len(cluster) / 20),
                source="x_cluster",
            ))
        return columns

    def _map_row(self, row: LayoutRow, columns: list[ColumnCandidate]) -> dict[str, list[LayoutToken]]:
        mapped: dict[str, list[LayoutToken]] = {}
        for token in row.tokens:
            column = self._nearest_column(token, columns)
            key = column.name if column else "unmapped"
            mapped.setdefault(key, []).append(token)
        return mapped

    def _nearest_column(self, token: LayoutToken, columns: list[ColumnCandidate]) -> ColumnCandidate | None:
        if not columns:
            return None
        containing = [column for column in columns if column.x_min <= token.x_center <= column.x_max]
        if containing:
            return min(containing, key=lambda column: abs(column.x_center - token.x_center))
        return min(columns, key=lambda column: abs(column.x_center - token.x_center))

    def _looks_like_item_or_candidate(self, row: LayoutRow, mapped: dict[str, list[LayoutToken]]) -> bool:
        text = row.text.strip()
        if not text or self._is_footer_or_note_row(row):
            return False
        has_alpha = bool(re.search(r"[A-Za-z가-힣]", text))
        numbers = _number_tokens(text)
        if mapped.get("item_name") and has_alpha:
            return True
        if mapped.get("quantity") or mapped.get("supply_amount") or mapped.get("line_total"):
            return True
        return has_alpha and len(numbers) >= 2

    def _structured_row(self, row: LayoutRow, mapped: dict[str, list[LayoutToken]]) -> StructuredTableRow:
        fields: dict[str, Any] = {}
        review_flags: list[str] = []
        untrusted_fields: list[str] = []

        item_name = self._text_for(mapped, "item_name")
        if item_name and _is_amount_like_token(item_name):
            item_name = None
        if not item_name:
            item_name = self._fallback_item_name(row)
        if item_name:
            fields["item_name"] = item_name

        for text_field in ("document_item_code", "internal_item_code", "specification", "unit"):
            value = self._text_for(mapped, text_field)
            if value:
                fields[text_field] = value

        for numeric_field in ("quantity", "unit_price", "supply_amount", "tax_amount", "line_total"):
            value = self._number_for(mapped, numeric_field)
            if value is not None:
                fields[numeric_field] = value
                if numeric_field in {"supply_amount", "tax_amount", "line_total"}:
                    untrusted_fields.append(numeric_field)

        if not any(key in fields for key in ("quantity", "unit_price", "supply_amount", "tax_amount", "line_total")):
            numbers = [_parse_number(value) for value in _number_tokens(row.text)]
            numbers = [value for value in numbers if value is not None]
            if numbers and not item_name:
                fields["line_total"] = numbers[-1]
                untrusted_fields.append("line_total")
                review_flags.append("fax_row_boundary_uncertain")
            elif len(numbers) >= 3 and self._sparse_amount_row(row):
                fields.setdefault("line_total", numbers[-1])
                untrusted_fields.append("line_total")
                review_flags.append("fax_row_boundary_uncertain")

        if not fields.get("item_name") and any(key in fields for key in ("quantity", "supply_amount", "tax_amount", "line_total")):
            review_flags.append("missing_item_name_from_ocr")
        if any(token.confidence < 0.8 for token in row.tokens):
            review_flags.append("low_ocr_confidence")
        if len(row.tokens) <= 3 and any(key in fields for key in ("supply_amount", "tax_amount", "line_total")):
            review_flags.append("row_boundary_uncertain")

        missing = []
        if not fields.get("item_name"):
            missing.append("item_name")
        bbox_span = _bbox_span(row.tokens)
        return StructuredTableRow(
            row_index=row.row_index,
            page=row.page,
            fields=fields,
            confidence=row.confidence,
            missing_fields=missing,
            untrusted_fields=untrusted_fields,
            review_flags=sorted(set(review_flags)),
            source_tokens=[asdict(token) for token in row.tokens],
            bbox_span=bbox_span,
        )

    def _is_footer_or_note_row(self, row: LayoutRow) -> bool:
        text = _compact(row.text)
        if any(_compact(term) in text for term in FOOTER_TERMS) and not self._sparse_amount_row(row):
            return True
        if re.search(r"^(공급가액|세액|합계금액|total|subtotal)\s*[0-9,]+$", row.text.strip(), re.IGNORECASE):
            return True
        if row.text.strip() in {"공급가액", "세액", "합계금액"}:
            return True
        return False

    def _text_for(self, mapped: dict[str, list[LayoutToken]], field: str) -> str | None:
        tokens = mapped.get(field) or []
        text = " ".join(token.text for token in sorted(tokens, key=lambda item: item.x_center)).strip()
        return text or None

    def _number_for(self, mapped: dict[str, list[LayoutToken]], field: str) -> int | float | None:
        text = self._text_for(mapped, field) or ""
        numbers = _number_tokens(text)
        if not numbers:
            return None
        return _parse_number(numbers[-1])

    def _fallback_item_name(self, row: LayoutRow) -> str | None:
        text_tokens = [
            token.text
            for token in row.tokens
            if re.search(r"[A-Za-z가-힣]", token.text)
            and token.text.upper() not in UNIT_TOKENS
            and not _is_amount_like_token(token.text)
            and not _compact(token.text) in {_compact(term) for term in FOOTER_TERMS}
        ]
        if not text_tokens:
            return None
        return " ".join(text_tokens).strip()

    def _sparse_amount_row(self, row: LayoutRow) -> bool:
        text = row.text
        numbers = _number_tokens(text)
        has_item_text = any(
            re.search(r"[A-Za-z가-힣]", token.text)
            and token.text.upper() not in UNIT_TOKENS
            and not _is_amount_like_token(token.text)
            and _compact(token.text) not in {_compact(term) for term in FOOTER_TERMS}
            for token in row.tokens
        )
        return len(numbers) >= 3 and has_item_text


def rows_to_debug(rows: list[LayoutRow]) -> list[dict[str, Any]]:
    return [
        {
            "row_index": row.row_index,
            "page": row.page,
            "y_min": row.y_min,
            "y_max": row.y_max,
            "y_center": row.y_center,
            "text": row.text,
            "confidence": row.confidence,
            "tokens": [asdict(token) for token in row.tokens],
        }
        for row in rows
    ]


def columns_to_debug(columns: list[ColumnCandidate]) -> list[dict[str, Any]]:
    return [asdict(column) for column in columns]


def structured_rows_to_debug(rows: list[StructuredTableRow]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]


def _rect_from_candidate(candidate: dict[str, Any]) -> dict[str, float] | None:
    if all(key in candidate for key in ("x_min", "y_min", "x_max", "y_max")):
        try:
            return {
                "x_min": float(candidate["x_min"]),
                "y_min": float(candidate["y_min"]),
                "x_max": float(candidate["x_max"]),
                "y_max": float(candidate["y_max"]),
            }
        except (TypeError, ValueError):
            return None
    bbox = candidate.get("bbox")
    if not isinstance(bbox, list):
        return None
    points = []
    for point in bbox:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"x_min": min(xs), "y_min": min(ys), "x_max": max(xs), "y_max": max(ys)}


def _bbox_span(tokens: list[LayoutToken]) -> dict[str, float] | None:
    if not tokens:
        return None
    return {
        "x_min": min(token.x_min for token in tokens),
        "y_min": min(token.y_min for token in tokens),
        "x_max": max(token.x_max for token in tokens),
        "y_max": max(token.y_max for token in tokens),
    }


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _number_tokens(value: str) -> list[str]:
    return re.findall(r"-?\d[\d,]*(?:\.\d+)?", value or "")


def _parse_number(value: str) -> int | float | None:
    try:
        cleaned = value.replace(",", "")
        number = float(cleaned)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _is_amount_like_token(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?[A-Za-z]?", stripped):
        return True
    if re.fullmatch(r"[0O]+[CG]?", stripped, re.IGNORECASE):
        return True
    return False


def _float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
