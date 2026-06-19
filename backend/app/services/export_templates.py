from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, ExportTemplate
from app.services.canonical_schema import get_exportable_fields


SOURCE_FIELD_OPTIONS = get_exportable_fields()


DEFAULT_TEMPLATES = [
    {
        "name": "기본 문서 양식",
        "description": "Docparse 기본 업무데이터 출력 양식입니다.",
        "is_default": True,
        "columns": [
            {"header": "문서유형", "source_field": "document_type"},
            {"header": "문서번호", "source_field": "document_number"},
            {"header": "거래일자", "source_field": "document_date"},
            {"header": "거래처", "source_field": "customer_name"},
            {"header": "공급업체", "source_field": "supplier_name"},
            {"header": "품목명", "source_field": "line_items.item_name"},
            {"header": "규격", "source_field": "line_items.specification"},
            {"header": "수량", "source_field": "line_items.quantity"},
            {"header": "단위", "source_field": "line_items.unit"},
            {"header": "단가", "source_field": "line_items.unit_price"},
            {"header": "공급가액", "source_field": "line_items.supply_amount"},
            {"header": "세액", "source_field": "line_items.tax_amount"},
            {"header": "합계", "source_field": "line_items.line_total"},
            {"header": "검토상태", "source_field": "review_status"},
        ],
    },
    {
        "name": "더존 업로드용",
        "description": "품목 행 기준으로 거래일자, 거래처, 품목, 금액을 정렬합니다.",
        "is_default": False,
        "columns": [
            {"header": "거래일자", "source_field": "document_date"},
            {"header": "거래처", "source_field": "customer_name"},
            {"header": "품목명", "source_field": "line_items.item_name"},
            {"header": "규격", "source_field": "line_items.specification"},
            {"header": "수량", "source_field": "line_items.quantity"},
            {"header": "단가", "source_field": "line_items.unit_price"},
            {"header": "공급가액", "source_field": "line_items.supply_amount"},
            {"header": "세액", "source_field": "line_items.tax_amount"},
            {"header": "합계", "source_field": "line_items.line_total"},
            {"header": "비고", "source_field": "line_items.note"},
        ],
    },
    {
        "name": "현장 검토용",
        "description": "금액보다 품목, 규격, 수량, 검사 결과 확인에 맞춘 양식입니다.",
        "is_default": False,
        "columns": [
            {"header": "문서번호", "source_field": "document_number"},
            {"header": "납기/기한", "source_field": "due_date"},
            {"header": "품목명", "source_field": "line_items.item_name"},
            {"header": "규격", "source_field": "line_items.specification"},
            {"header": "Lot/Code", "source_field": "line_items.lot_code"},
            {"header": "수량", "source_field": "line_items.quantity"},
            {"header": "입고수량", "source_field": "line_items.received_quantity"},
            {"header": "합격수량", "source_field": "line_items.accepted_quantity"},
            {"header": "불량수량", "source_field": "line_items.defective_quantity"},
            {"header": "판정", "source_field": "line_items.inspection_result"},
            {"header": "비고", "source_field": "line_items.note"},
        ],
    },
]


def ensure_default_export_templates(db: Session) -> None:
    if db.scalar(select(ExportTemplate.id).limit(1)):
        return
    for payload in DEFAULT_TEMPLATES:
        db.add(
            ExportTemplate(
                name=payload["name"],
                description=payload.get("description"),
                is_default=bool(payload.get("is_default")),
                scope="global",
                template_columns=normalize_template_columns(payload.get("columns")),
            )
        )
    db.commit()


def normalize_template_columns(columns: Any) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(columns, list):
        return normalized
    for index, column in enumerate(columns, start=1):
        if not isinstance(column, dict):
            continue
        header = str(column.get("header") or "").strip() or f"컬럼 {index}"
        source_field = str(column.get("source_field") or "__blank__").strip()
        column_type = str(column.get("column_type") or "field").strip()
        if source_field == "__blank__":
            column_type = "blank"
        elif source_field == "__static__":
            column_type = "static"
        if column_type not in {"field", "static", "blank"}:
            column_type = "field"
        normalized.append(
            {
                "header": header[:120],
                "source_field": source_field[:160],
                "column_type": column_type,
                "static_value": _blank_if_none(column.get("static_value")),
            }
        )
    return normalized


def export_template_to_read(template: ExportTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "scope": template.scope,
        "is_default": template.is_default,
        "columns": normalize_template_columns(template.template_columns),
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def apply_default_flag(db: Session, template: ExportTemplate, is_default: bool | None) -> None:
    if not is_default:
        template.is_default = bool(is_default) if is_default is not None else template.is_default
        return
    db.query(ExportTemplate).filter(ExportTemplate.id != template.id, ExportTemplate.scope == template.scope).update({"is_default": False})
    template.is_default = True


def documents_to_template_rows(documents: list[Document], template: ExportTemplate) -> list[dict]:
    columns = normalize_template_columns(template.template_columns)
    rows: list[dict] = []
    for document in documents:
        line_items = document.line_items or [{}]
        for item in line_items:
            row: dict[str, object] = {}
            for column in columns:
                row[column["header"]] = _column_value(document, item, column)
            row["_party_tab"] = document.customer_name or document.vendor_name or document.merchant_name or "미분류"
            rows.append(row)
    return rows


def _column_value(document: Document, line_item: dict, column: dict) -> object:
    column_type = column.get("column_type")
    if column_type == "blank":
        return ""
    if column_type == "static":
        return column.get("static_value") or ""
    source = str(column.get("source_field") or "")
    if source.startswith("line_items."):
        return _json_scalar(line_item.get(source.split(".", 1)[1]))
    return _json_scalar(_document_value(document, source))


def _document_value(document: Document, source: str) -> object:
    if source == "document_type":
        return getattr(document.document_type, "value", str(document.document_type))
    if source == "document_number":
        return document.document_number
    if source == "document_date":
        return document.issue_date or document.extracted_date
    if source == "due_date":
        return document.due_date
    if source == "supplier_name":
        return document.vendor_name or document.merchant_name
    if source == "customer_name":
        return document.customer_name
    if source == "total_amount":
        return document.extracted_amount
    if source == "tax_amount":
        return document.tax
    if source == "currency":
        return document.currency
    if source == "review_status":
        return "검토 필요" if document.review_required else "확정 가능"
    if source == "source_filename":
        return document.original_filename
    if source == "created_at":
        return document.created_at
    return None


def _json_scalar(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _blank_if_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
