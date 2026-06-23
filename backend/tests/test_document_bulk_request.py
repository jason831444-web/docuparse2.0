from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.document import DocumentType, ProcessingStatus
from app.schemas.document import BulkDocumentRequest, DocumentRead


def test_bulk_document_request_accepts_up_to_5000_ids():
    payload = BulkDocumentRequest(ids=[uuid4() for _ in range(5000)])

    assert len(payload.ids) == 5000


def test_bulk_document_request_rejects_more_than_5000_ids():
    with pytest.raises(ValidationError):
        BulkDocumentRequest(ids=[uuid4() for _ in range(5001)])


def test_document_read_allows_negative_adjustment_document_amounts():
    payload = {
        "id": uuid4(),
        "original_filename": "credit-note.pdf",
        "stored_file_path": "/tmp/credit-note.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.transaction_statement,
        "extracted_amount": "-92.00",
        "subtotal": "-3.00",
        "tax": "-0.30",
        "line_items": [],
        "processing_status": ProcessingStatus.needs_review,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
        "file_url": "http://localhost/uploads/credit-note.pdf",
    }

    document = DocumentRead.model_validate(payload)

    assert document.extracted_amount is not None
    assert document.subtotal is not None
    assert document.tax is not None
    assert document.extracted_amount < 0
    assert document.subtotal < 0
    assert document.tax < 0
