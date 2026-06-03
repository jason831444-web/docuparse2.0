import io
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pandas as pd

from app.models.document import Document


def serialize_document(document: Document) -> dict:
    data = {
        column.name: getattr(document, column.name)
        for column in document.__table__.columns
        if column.name not in {"stored_file_path"}
    }
    for key, value in data.items():
        if isinstance(value, (datetime, date, UUID, Decimal)):
            data[key] = str(value)
        elif hasattr(value, "value"):
            data[key] = value.value
    return data


def documents_to_csv(documents: list[Document]) -> str:
    rows = documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows)
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue()


def documents_to_excel(documents: list[Document]) -> bytes:
    rows = documents_to_erp_rows(documents)
    frame = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="erp_ready_data")
    return buffer.getvalue()


def document_to_json(document: Document) -> str:
    return json.dumps(serialize_document(document), indent=2)


def documents_to_erp_rows(documents: list[Document]) -> list[dict]:
    rows: list[dict] = []
    for document in documents:
        line_items = document.line_items or [{}]
        for item in line_items:
            rows.append({
                "문서유형": getattr(document.document_type, "value", str(document.document_type)),
                "공급업체": document.vendor_name or document.merchant_name,
                "고객사": document.customer_name,
                "문서번호": document.document_number,
                "발행일": str(document.issue_date or document.extracted_date or "") or None,
                "납기일": str(document.due_date or "") or None,
                "품목명": item.get("item_name"),
                "품목코드": item.get("item_code"),
                "규격": item.get("specification"),
                "수량": item.get("quantity"),
                "단위": item.get("unit"),
                "단가": item.get("unit_price"),
                "공급가액": item.get("supply_amount"),
                "세액": item.get("tax_amount"),
                "합계금액": item.get("line_total") or document.extracted_amount,
                "통화": document.currency,
                "검토상태": "검토 필요" if document.review_required else "확정 가능",
            })
    return rows
