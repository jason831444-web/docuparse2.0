from __future__ import annotations

import re
from typing import Any


DOCUMENT_FIELDS: tuple[dict[str, str], ...] = (
    {"value": "document_type", "label": "문서 유형", "group": "문서"},
    {"value": "document_number", "label": "문서번호", "group": "문서"},
    {"value": "document_date", "label": "거래일자", "group": "문서"},
    {"value": "due_date", "label": "납기일/기한", "group": "문서"},
    {"value": "supplier_name", "label": "공급업체", "group": "문서"},
    {"value": "customer_name", "label": "거래처/고객사", "group": "문서"},
    {"value": "total_amount", "label": "문서 합계", "group": "문서"},
    {"value": "tax_amount", "label": "문서 세액", "group": "문서"},
    {"value": "currency", "label": "통화", "group": "문서"},
    {"value": "review_status", "label": "검토 상태", "group": "문서"},
    {"value": "source_filename", "label": "원본 파일명", "group": "문서"},
    {"value": "created_at", "label": "업로드일시", "group": "문서"},
)

LINE_ITEM_FIELDS: tuple[dict[str, str], ...] = (
    {"value": "item_name", "label": "품목명", "group": "품목 행"},
    {"value": "specification", "label": "규격", "group": "품목 행"},
    {"value": "item_code", "label": "품목코드", "group": "품목 행"},
    {"value": "document_item_code", "label": "문서 품목코드", "group": "품목 행"},
    {"value": "internal_item_code", "label": "내부 품목코드", "group": "품목 행"},
    {"value": "quantity", "label": "수량", "group": "품목 행"},
    {"value": "unit", "label": "단위", "group": "품목 행"},
    {"value": "unit_price", "label": "단가", "group": "품목 행"},
    {"value": "supply_amount", "label": "공급가액", "group": "품목 행"},
    {"value": "tax_amount", "label": "세액", "group": "품목 행"},
    {"value": "line_total", "label": "합계", "group": "품목 행"},
    {"value": "note", "label": "비고", "group": "품목 행"},
    {"value": "lot_code", "label": "Lot/Code", "group": "품목 행"},
    {"value": "received_quantity", "label": "입고수량", "group": "품목 행"},
    {"value": "accepted_quantity", "label": "합격수량", "group": "품목 행"},
    {"value": "defective_quantity", "label": "불량수량", "group": "품목 행"},
    {"value": "inspection_item", "label": "검사항목", "group": "품목 행"},
    {"value": "inspection_result", "label": "검사판정", "group": "품목 행"},
    {"value": "result", "label": "판정/결과", "group": "품목 행"},
    {"value": "judgment", "label": "판정", "group": "품목 행"},
    {"value": "defect_reason", "label": "불량 사유", "group": "품목 행"},
)

CUSTOM_EXPORT_FIELDS: tuple[dict[str, str], ...] = (
    {"value": "__blank__", "label": "빈 컬럼", "group": "사용자 지정"},
    {"value": "__static__", "label": "고정값 컬럼", "group": "사용자 지정"},
)

NUMERIC_FIELDS = {
    "no",
    "quantity",
    "received_quantity",
    "accepted_quantity",
    "defective_quantity",
    "requested_quantity",
    "delivered_quantity",
}
AMOUNT_FIELDS = {"unit_price", "supply_amount", "tax_amount", "line_total"}
INSPECTION_AMOUNT_FIELDS = {"unit_price", "supply_amount", "tax_amount", "line_total", "subtotal", "total", "currency"}

HEADER_ALIASES: dict[str, str] = {
    "no": "no",
    "번호": "no",
    "품명": "item_name",
    "품목": "item_name",
    "품목명": "item_name",
    "제품명": "item_name",
    "item": "item_name",
    "itemname": "item_name",
    "description": "item_name",
    "규격": "specification",
    "모델": "specification",
    "모델명": "specification",
    "spec": "specification",
    "specification": "specification",
    "lot": "lot_code",
    "lotcode": "document_item_code",
    "lotno": "lot_code",
    "loucode": "document_item_code",
    "code": "document_item_code",
    "품목코드": "document_item_code",
    "문서품목코드": "document_item_code",
    "itemcode": "item_code",
    "internalitemcode": "internal_item_code",
    "내부품목코드": "internal_item_code",
    "입고수량": "received_quantity",
    "합격": "accepted_quantity",
    "합격수량": "accepted_quantity",
    "불량": "defective_quantity",
    "불량수량": "defective_quantity",
    "검사항목": "inspection_item",
    "검사결과": "inspection_result",
    "판정": "result",
    "결과": "result",
    "judgment": "judgment",
    "defectreason": "defect_reason",
    "불량사유": "defect_reason",
    "비고": "note",
    "remark": "note",
    "remarks": "note",
    "note": "note",
    "수량": "quantity",
    "qty": "quantity",
    "quantity": "quantity",
    "납품수량": "delivered_quantity",
    "요청수량": "requested_quantity",
    "발주수량": "requested_quantity",
    "단위": "unit",
    "unit": "unit",
    "단가": "unit_price",
    "unitprice": "unit_price",
    "unitcost": "unit_price",
    "공급가액": "supply_amount",
    "supplyamount": "supply_amount",
    "세액": "tax_amount",
    "vat": "tax_amount",
    "tax": "tax_amount",
    "합계": "line_total",
    "합계금액": "line_total",
    "금액": "line_total",
    "amount": "line_total",
    "linetotal": "line_total",
    "total": "line_total",
}


def get_document_fields() -> list[dict[str, str]]:
    return [dict(field) for field in DOCUMENT_FIELDS]


def get_line_item_fields() -> list[dict[str, str]]:
    return [dict(field) for field in LINE_ITEM_FIELDS]


def get_exportable_fields() -> list[dict[str, str]]:
    fields = get_document_fields()
    fields.extend(
        {
            "value": f"line_items.{field['value']}",
            "label": field["label"],
            "group": field["group"],
        }
        for field in LINE_ITEM_FIELDS
    )
    fields.extend(dict(field) for field in CUSTOM_EXPORT_FIELDS)
    return fields


def normalize_header_key(header: str | None) -> str:
    return re.sub(r"[\s_:/()\\-]+", "", str(header or "").casefold())


def canonical_field_for_header(header: str | None) -> str | None:
    return HEADER_ALIASES.get(normalize_header_key(header))


def expected_column_groups(document_type: str) -> list[tuple[str, set[str]]]:
    if document_type == "inspection_report":
        return [
            ("품명", {"item_name"}),
            ("Lot/Code", {"lot_code", "document_item_code"}),
            ("입고수량", {"received_quantity"}),
            ("합격수량 또는 판정", {"accepted_quantity", "result", "inspection_result"}),
            ("불량수량 또는 비고", {"defective_quantity", "note"}),
            ("검사항목", {"inspection_item"}),
            ("판정", {"result", "inspection_result", "judgment"}),
            ("비고", {"note"}),
        ]
    if document_type == "delivery_note":
        return [
            ("품명", {"item_name"}),
            ("규격", {"specification"}),
            ("수량", {"quantity", "delivered_quantity", "requested_quantity"}),
            ("단위", {"unit"}),
            ("비고", {"note"}),
        ]
    if document_type in {"invoice", "transaction_statement", "purchase_order", "quotation"}:
        return [
            ("품명", {"item_name"}),
            ("규격", {"specification"}),
            ("수량", {"quantity"}),
            ("단가", {"unit_price"}),
            ("공급가액", {"supply_amount"}),
            ("세액", {"tax_amount"}),
            ("합계", {"line_total"}),
        ]
    return [
        ("품명", {"item_name"}),
        ("규격", {"specification"}),
        ("수량", {"quantity", "delivered_quantity", "requested_quantity"}),
    ]


def canonicalize_official_table_row(
    columns: list[str],
    raw_row: list[str],
    table_type: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "raw_cells": {columns[index] if index < len(columns) else f"column_{index + 1}": value for index, value in enumerate(raw_row)}
    }
    for index, value in enumerate(raw_row):
        header = columns[index] if index < len(columns) else ""
        canonical = canonical_field_for_header(header)
        cell = clean_cell(value)
        if not canonical or cell in (None, ""):
            continue
        if canonical in NUMERIC_FIELDS:
            parsed = int_text(cell)
            if parsed is not None:
                row[canonical] = parsed
            else:
                row.setdefault("review_flags", []).append(f"{canonical}_parse_review_required")
                row[canonical] = cell
        elif canonical in AMOUNT_FIELDS:
            if table_type == "incoming_inspection":
                row.setdefault("review_flags", []).append("inspection_report_amount_field_removed")
                continue
            parsed = int_text(cell)
            row[canonical] = parsed if parsed is not None else cell
        else:
            row[canonical] = re.sub(r"조건부\s*합격|조건부합격", "조건부 합격", cell).strip() if canonical == "result" else cell
    if table_type == "incoming_inspection":
        for amount_field in INSPECTION_AMOUNT_FIELDS:
            row.pop(amount_field, None)
        row.setdefault("review_flags", []).append("paddleocrvl_official_table_review_required")
        row.setdefault("review_flags", []).append("vl_schema_prompt_inspection_review_required")
        row.update(split_official_inspection_item_fields(row))
        if not row.get("item_name") or inspection_header_or_note(str(row.get("item_name"))):
            return {}
        row["review_flags"] = sorted(set(str(flag) for flag in row.get("review_flags") or [] if flag))
    elif not row.get("item_name"):
        return {}
    return row


def canonicalize_row(raw_row: dict[str, Any], *, table_type: str = "line_items") -> dict[str, Any]:
    columns = list(raw_row.keys())
    values = ["" if raw_row[column] is None else str(raw_row[column]) for column in columns]
    return canonicalize_official_table_row(columns, values, table_type)


def split_official_inspection_item_fields(row: dict[str, Any]) -> dict[str, Any]:
    item_name = clean_cell(str(row.get("item_name") or ""))
    if not item_name:
        return {}
    if row.get("document_item_code") and row.get("specification"):
        return {}
    tokens = item_name.split()
    if len(tokens) < 2:
        return {}
    code_index: int | None = None
    spec_index: int | None = None
    for index, token in enumerate(tokens):
        if not row.get("document_item_code") and re.fullmatch(r"[A-Z]{2,8}(?:[-_][A-Z0-9]{1,12})+", token, flags=re.IGNORECASE):
            code_index = index
        if not row.get("specification") and re.fullmatch(r"(?:M\d+(?:[xX]\d+)?|\d+[xX]\d+(?:[xX]\d+)?|\d+(?:mm|T|P)|\d+P)", token, flags=re.IGNORECASE):
            spec_index = index
    updates: dict[str, Any] = {}
    cut_indexes = [index for index in (code_index, spec_index) if index is not None]
    if code_index is not None:
        updates["document_item_code"] = tokens[code_index]
    if spec_index is not None:
        updates["specification"] = tokens[spec_index]
    if cut_indexes:
        cut = min(cut_indexes)
        cleaned_name = " ".join(tokens[:cut]).strip()
        if cleaned_name:
            updates["item_name"] = cleaned_name
    return updates


def inspection_header_or_note(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if re.search(r"^(No|번호)?품목(?:명)?규격입고수량합격(?:수량)?불량(?:수량)?판정", compact, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(r"(검사의견|금액항목없음|금액정보없음|문서번호|검사일|협력사|검사자|품질팀)", compact)
        and not re.match(r"^\d{1,3}\s+", line)
    )


def int_text(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", ""))
    except Exception:
        return None


def clean_cell(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -:：")
    return text or None
