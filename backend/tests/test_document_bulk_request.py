from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.document import BulkDocumentRequest


def test_bulk_document_request_accepts_up_to_500_ids():
    payload = BulkDocumentRequest(ids=[uuid4() for _ in range(500)])

    assert len(payload.ids) == 500


def test_bulk_document_request_rejects_more_than_500_ids():
    with pytest.raises(ValidationError):
        BulkDocumentRequest(ids=[uuid4() for _ in range(501)])
