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
    rows = [serialize_document(document) for document in documents]
    frame = pd.DataFrame(rows)
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue()


def documents_to_excel(documents: list[Document]) -> bytes:
    rows = [serialize_document(document) for document in documents]
    frame = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="documents")
    return buffer.getvalue()


def document_to_json(document: Document) -> str:
    return json.dumps(serialize_document(document), indent=2)
