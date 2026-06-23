from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.document import Document


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
        tables = self._reviewed_line_item_tables(document) or self._raw_tables(metadata)
        existing_key_values = self._existing_key_values(metadata)
        if reviewed_key_values is not None:
            key_values = self._reviewed_key_values(existing_key_values, reviewed_key_values)
        elif existing_key_values and source in {"manual_update", "confirmed_review"}:
            key_values = existing_key_values
        else:
            self._add_vl_direct_key_values(metadata, key_values)
            self._add_ocr_line_key_values(line_candidates or [], key_values)
            self._add_current_document_fields(document, key_values)
            self._add_pos_summary(metadata, key_values)
            self._add_ai_parsed_key_values(metadata, key_values)
            self._add_vl_document_key_values(metadata, key_values)
            self._add_candidate_values(metadata, key_values)

        return {
            "version": "raw_extraction_v1",
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key_values": self._dedupe_key_values(key_values),
            "tables": tables,
        }

    def _add_current_document_fields(self, document: Document, key_values: list[dict[str, Any]]) -> None:
        fields = {
            "문서유형": getattr(document.document_type, "value", str(document.document_type or "")),
            "문서번호": document.document_number,
            "제목": document.title,
            "공급업체": document.vendor_name or document.merchant_name,
            "고객사": document.customer_name,
            "발행일": document.issue_date or document.extracted_date,
            "납기일": document.due_date,
            "공급가액": document.subtotal,
            "세액": document.tax,
            "합계금액": document.extracted_amount,
            "통화": document.currency,
        }
        for key, value in fields.items():
            self._append_key_value(key_values, key, value, "confirmed_document_field")

    def _add_ocr_line_key_values(self, line_candidates: list[dict[str, Any]], key_values: list[dict[str, Any]]) -> None:
        if not line_candidates:
            return
        scale_x = max([self._candidate_coord(candidate, "x_max") or 0 for candidate in line_candidates] + [1.0])
        scale_y = max([self._candidate_coord(candidate, "y_max") or 0 for candidate in line_candidates] + [1.0])
        for candidate in line_candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text") or "").strip()
            parsed = self._parse_key_value_line(text)
            if not parsed:
                continue
            key, value = parsed
            bbox = self._line_candidate_bbox(candidate, scale_x=scale_x, scale_y=scale_y)
            key_bbox, value_bbox = self._split_key_value_bbox(text, key, bbox)
            self._append_key_value(
                key_values,
                key,
                value,
                "ocr_line_bbox",
                confidence=candidate.get("confidence"),
                bbox=bbox,
                page_index=candidate.get("page_index") or candidate.get("page"),
                key_bbox=key_bbox,
                value_bbox=value_bbox,
            )

    def _parse_key_value_line(self, text: str) -> tuple[str, str] | None:
        if not text or len(text) > 160:
            return None
        match = re.match(r"^\s*([^:：]{1,40})\s*[:：]\s*(.{1,80})\s*$", text)
        if not match:
            match = re.match(r"^\s*([가-힣A-Za-z0-9/().\s]{1,30})\s{2,}(.{1,80})\s*$", text)
        if not match:
            return None
        key = re.sub(r"\s+", " ", match.group(1)).strip()
        value = match.group(2).strip()
        if not key or not value:
            return None
        if len(key) > 40 or len(value) > 100:
            return None
        if re.fullmatch(r"[-_./\\|]+", key) or re.fullmatch(r"[-_./\\|]+", value):
            return None
        return key, value

    def _add_pos_summary(self, metadata: dict[str, Any], key_values: list[dict[str, Any]]) -> None:
        summary = metadata.get("pos_settlement_summary") if isinstance(metadata.get("pos_settlement_summary"), dict) else {}
        for key, value in summary.items():
            self._append_key_value(key_values, str(key), value, "pos_settlement_summary")

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
                    "vl_direct_key_value_bbox",
                    role=str(item.get("role") or item.get("field") or "") or None,
                    confidence=item.get("confidence"),
                    section=str(item.get("section") or item.get("group") or "") or None,
                    bbox=self._bbox_from_item(item),
                    page_index=self._page_index_from_item(item),
                    key_bbox=item.get("key_bbox"),
                    value_bbox=item.get("value_bbox"),
                )

    def _add_ai_parsed_key_values(self, metadata: dict[str, Any], key_values: list[dict[str, Any]]) -> None:
        ai = metadata.get("ai_parsed_document") if isinstance(metadata.get("ai_parsed_document"), dict) else {}
        for section in ai.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_title = section.get("title") or section.get("section_title")
            for item in section.get("items") or section.get("key_values") or []:
                if not isinstance(item, dict):
                    continue
                key = item.get("key") or item.get("label") or item.get("field") or item.get("name")
                value = item.get("value") if item.get("value") is not None else item.get("normalized_value")
                self._append_key_value(
                    key_values,
                    key,
                    value,
                    "ai_parsed_document",
                    section=str(section_title or "") or None,
                    bbox=self._bbox_from_item(item),
                    page_index=self._page_index_from_item(item),
                )

    def _add_vl_document_key_values(self, metadata: dict[str, Any], key_values: list[dict[str, Any]]) -> None:
        for candidate in self._vl_candidates(metadata):
            structured = candidate.get("structured_candidate") if isinstance(candidate.get("structured_candidate"), dict) else {}
            document = structured.get("document") if isinstance(structured.get("document"), dict) else {}
            for key, value in document.items():
                if isinstance(value, (dict, list)):
                    continue
                self._append_key_value(key_values, str(key), value, "vl_structured_document")

    def _add_candidate_values(self, metadata: dict[str, Any], key_values: list[dict[str, Any]]) -> None:
        for bucket in (
            "party_review_candidates",
            "document_number_candidates",
            "date_candidates",
            "amount_candidates",
            "tax_amount_candidates",
        ):
            values = metadata.get(bucket)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                key = item.get("source_label") or item.get("label") or item.get("field") or item.get("role") or bucket
                value = item.get("normalized_value") if item.get("normalized_value") is not None else item.get("value")
                self._append_key_value(
                    key_values,
                    key,
                    value,
                    bucket,
                    role=str(item.get("role") or item.get("field") or "") or None,
                    confidence=item.get("confidence"),
                    bbox=self._bbox_from_item(item),
                    page_index=self._page_index_from_item(item),
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
                tables.append({
                    "table_type": table.get("table_type") or "table",
                    "source": table.get("source") or "unknown",
                    "columns": columns,
                    "rows": raw_rows,
                    "row_count": len(raw_rows),
                })
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
        normalized: list[dict[str, Any]] = []
        if raw_rows:
            for row in raw_rows:
                if isinstance(row, dict):
                    normalized.append({str(key): self._json_value(value) for key, value in row.items()})
                elif isinstance(row, list):
                    normalized.append({str(index + 1): self._json_value(value) for index, value in enumerate(row)})
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
        reviewed_by_id = {self._key_value_identity(item): item for item in reviewed if isinstance(item, dict)}
        result: list[dict[str, Any]] = []
        for item in existing:
            identity = self._key_value_identity(item)
            replacement = reviewed_by_id.get(identity)
            if replacement is None:
                result.append(dict(item))
                continue
            next_item = dict(item)
            next_item["value"] = self._json_value(replacement.get("value"))
            next_item["reviewed"] = True
            result.append(next_item)
        return result

    def _key_value_identity(self, item: dict[str, Any]) -> str:
        return "|".join(str(item.get(key) or "") for key in ("key", "source", "role", "section"))

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
        if normalized_bbox:
            item["normalized_bbox"] = normalized_bbox
        normalized_key_bbox = self._normalize_bbox(key_bbox)
        if normalized_key_bbox:
            item["key_bbox"] = normalized_key_bbox
        normalized_value_bbox = self._normalize_bbox(value_bbox)
        if normalized_value_bbox:
            item["value_bbox"] = normalized_value_bbox
        if page_index not in (None, ""):
            item["page_index"] = self._json_value(page_index)
        key_values.append(item)

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

    def _json_value(self, value: object) -> object:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value
