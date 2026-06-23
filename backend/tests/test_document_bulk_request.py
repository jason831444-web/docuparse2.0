import sys
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, UploadFile
from pydantic import ValidationError

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda *_args, **_kwargs: "",
        image_to_data=lambda *_args, **_kwargs: {"conf": []},
    ),
)

from app.api.routes import documents as document_routes
from app.models.document import DocumentType, ProcessingStatus
from app.schemas.document import BulkDocumentRequest, DocumentBatchUploadResponse, DocumentBulkStatusResponse, DocumentRead


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


def test_document_batch_upload_response_preserves_success_and_error_indexes():
    document_payload = {
        "id": uuid4(),
        "original_filename": "ok.pdf",
        "stored_file_path": "/tmp/ok.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.general_document,
        "line_items": [],
        "processing_status": ProcessingStatus.queued,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
        "file_url": "http://localhost/uploads/ok.pdf",
    }
    payload = {
        "items": [{"index": 1, "document": document_payload}],
        "errors": [{"index": 0, "filename": "bad.exe", "error": "Unsupported file type"}],
    }

    response = DocumentBatchUploadResponse.model_validate(payload)

    assert response.items[0].index == 1
    assert response.items[0].document.original_filename == "ok.pdf"
    assert response.errors[0].index == 0


def test_document_bulk_status_response_tracks_missing_ids():
    found_id = uuid4()
    missing_id = uuid4()
    document_payload = {
        "id": found_id,
        "original_filename": "queued.pdf",
        "stored_file_path": "/tmp/queued.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.general_document,
        "line_items": [],
        "processing_status": ProcessingStatus.processing,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
        "file_url": "http://localhost/uploads/queued.pdf",
    }

    response = DocumentBulkStatusResponse.model_validate({
        "items": [document_payload],
        "missing_ids": [missing_id],
    })

    assert response.items[0].id == found_id
    assert response.items[0].processing_status == ProcessingStatus.processing
    assert response.missing_ids == [missing_id]


def test_bulk_status_route_returns_found_documents_in_request_order(monkeypatch):
    first_id = uuid4()
    second_id = uuid4()
    missing_id = uuid4()
    stored_documents = [
        SimpleNamespace(id=second_id, original_filename="second.pdf"),
        SimpleNamespace(id=first_id, original_filename="first.pdf"),
    ]

    class FakeScalarResult:
        def all(self):
            return stored_documents

    class FakeDb:
        def scalars(self, _stmt):
            return FakeScalarResult()

    def fake_to_list_read(document: SimpleNamespace) -> DocumentRead:
        return DocumentRead.model_validate(
            {
                "id": document.id,
                "original_filename": document.original_filename,
                "stored_file_path": f"/tmp/{document.original_filename}",
                "mime_type": "application/pdf",
                "document_type": DocumentType.general_document,
                "line_items": [],
                "processing_status": ProcessingStatus.processing,
                "created_at": "2026-06-15T00:00:00Z",
                "updated_at": "2026-06-15T00:00:00Z",
                "file_url": f"http://localhost/uploads/{document.original_filename}",
            }
        )

    monkeypatch.setattr(document_routes, "_to_list_read", fake_to_list_read)

    response = document_routes.bulk_document_status(BulkDocumentRequest(ids=[first_id, missing_id, second_id]), FakeDb())

    assert [item.id for item in response.items] == [first_id, second_id]
    assert response.missing_ids == [missing_id]


def _upload(filename: str, content: bytes = b"body", content_type: str = "application/pdf") -> UploadFile:
    file = SpooledTemporaryFile()
    file.write(content)
    file.seek(0)
    return UploadFile(filename=filename, file=file, headers={"content-type": content_type})


def test_batch_upload_route_accepts_multiple_files_and_preserves_partial_failures(monkeypatch):
    created_filenames: list[str] = []

    def fake_create_uploaded_document(file: UploadFile, db: object) -> SimpleNamespace:
        created_filenames.append(file.filename or "")
        if file.filename == "bad.exe":
            raise ValueError("Unsupported file type")
        return SimpleNamespace(id=uuid4(), original_filename=file.filename, stored_file_path=f"/tmp/{file.filename}", mime_type=file.content_type)

    def fake_to_read(document: SimpleNamespace) -> DocumentRead:
        return DocumentRead.model_validate(
            {
                "id": document.id,
                "original_filename": document.original_filename,
                "stored_file_path": document.stored_file_path,
                "mime_type": document.mime_type,
                "document_type": DocumentType.general_document,
                "line_items": [],
                "processing_status": ProcessingStatus.queued,
                "created_at": "2026-06-15T00:00:00Z",
                "updated_at": "2026-06-15T00:00:00Z",
                "file_url": f"http://localhost/uploads/{document.original_filename}",
            }
        )

    monkeypatch.setattr(document_routes, "_create_uploaded_document", fake_create_uploaded_document)
    monkeypatch.setattr(document_routes, "_to_read", fake_to_read)
    monkeypatch.setattr(document_routes, "get_settings", lambda: SimpleNamespace(background_processing_enabled=False))

    response = document_routes.upload_documents_batch(
        [_upload("first.pdf"), _upload("bad.exe", b"bad", "application/octet-stream"), _upload("third.pdf")],
        BackgroundTasks(),
        db=object(),
    )

    assert created_filenames == ["first.pdf", "bad.exe", "third.pdf"]
    assert [item.index for item in response.items] == [0, 2]
    assert [item.document.original_filename for item in response.items] == ["first.pdf", "third.pdf"]
    assert response.errors[0].index == 1
    assert response.errors[0].filename == "bad.exe"
