import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.document_processor import DocumentProcessor


class FakeVLWorker:
    def __init__(self, payload: dict | None = None, *, enabled: bool = True) -> None:
        self.payload = payload or {}
        self._enabled = enabled
        self.calls: list[tuple[Path, str]] = []

    def enabled(self) -> bool:
        return self._enabled

    def analyze(self, file_path: Path, *, original_filename: str = "") -> dict:
        self.calls.append((file_path, original_filename))
        return dict(self.payload)


class FakeSession:
    def __init__(self, document: Document) -> None:
        self.document = document

    def add(self, document: Document) -> None:
        self.document = document

    def commit(self) -> None:
        return None

    def refresh(self, document: Document) -> None:
        return None

    def rollback(self) -> None:
        return None

    def get(self, model, document_id):
        return self.document


def _document(**kwargs) -> Document:
    defaults = {
        "original_filename": "vl-sample.pdf",
        "stored_file_path": "/tmp/vl-sample.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.quotation,
        "workflow_metadata": {
            "taxonomy": {
                "document_profile": "priced_document",
                "document_profiles": ["priced_document"],
            }
        },
        "line_items": [],
    }
    defaults.update(kwargs)
    return Document(**defaults)


def _processor(worker: FakeVLWorker) -> DocumentProcessor:
    processor = DocumentProcessor()
    processor.vl_worker = worker
    return processor


def test_vl_upload_pipeline_promotes_valid_worker_candidate_to_confirmed_fields():
    text = """
    견적서
    견적번호 QT-2026-0808-010
    공급업체 한성산업 고객사 제일기계
    견적일 2026-08-08 통화 KRW
    품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액
    스테인리스 브라켓 BRK-SUS-01 50x80x3T 100 EA 1500 150000 15000 165000
    총액 165,000
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "pass",
            "text": text,
            "elapsed_ms": 95000,
            "validation": {"status": "pass", "ok": True},
        }
    )
    document = _document()

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert worker.calls == [(Path("/tmp/vl-sample.pdf"), "vl-sample.pdf")]
    assert metadata is not None
    assert metadata["vl_candidate_summary"]["promotion_applied"] is True
    assert metadata["vl_candidate_summary"]["gate_decision"] == "promotion_eligible"
    assert metadata["vl_candidates"][0]["candidate_only"] is False
    assert metadata["vl_candidates"][0]["confirmed_promotion"] is True
    assert document.document_number == "QT-2026-0808-010"
    assert document.vendor_name == "한성산업"
    assert document.customer_name == "제일기계"
    assert document.currency == "KRW"
    assert document.extracted_amount == Decimal("165000")
    assert len(document.line_items or []) == 1
    assert document.line_items[0]["item_name"] == "스테인리스 브라켓"
    assert document.line_items[0]["quantity"] == 100


def test_vl_upload_pipeline_keeps_risky_candidate_out_of_confirmed_line_items():
    text = """
    견적서
    견적번호 QT-2026-0808-009
    공급업체 한성산업 고객사 제일기계
    견적일 2026-08-08 통화 KRW
    품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액
    고정 플레이트 PLT-FIX-02 120x60x5T EA 2800 280000 28000 308000
    스테인리스 브라켓 BRK-SUS-01 50x80x3T 100 EA 1500 150000 15000 165000
    총액 473,000
    첫 번째 품목 수량 공란
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "warn",
            "text": text,
            "validation": {"status": "warn", "ok": False},
        }
    )
    existing_items = [{"item_name": "기존 OCR 품목", "quantity": None}]
    document = _document(
        document_number="QT-2026-0808-009",
        extracted_amount=Decimal("473000"),
        currency="KRW",
        line_items=list(existing_items),
    )

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is not None
    assert metadata["vl_candidate_summary"]["promotion_applied"] is False
    assert metadata["vl_candidate_summary"]["requires_review"] is True
    assert metadata["vl_candidate_summary"]["gate_decision"] == "review_required"
    assert metadata["vl_candidates"][0]["candidate_only"] is True
    assert document.line_items == existing_items
    issue_codes = metadata["vl_candidate_summary"]["issue_codes"]
    assert "vl_candidate_requires_review" in issue_codes
    assert metadata["normalized_review_issues"][0]["code"] == "vl_candidate_review_required"


def test_vl_upload_pipeline_is_noop_when_worker_is_disabled():
    worker = FakeVLWorker(enabled=False)
    document = _document(document_number="QT-UNCHANGED")

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is None
    assert worker.calls == []
    assert document.document_number == "QT-UNCHANGED"


def test_process_uses_vl_first_and_skips_ppocr_ingestion_when_candidate_promotes(tmp_path):
    path = tmp_path / "quote.pdf"
    path.write_bytes(b"%PDF-1.4\n% fake test file not read when VL succeeds\n")
    text = """
    견적서
    견적번호 QT-2026-0808-010
    공급업체 한성산업 고객사 제일기계
    견적일 2026-08-08 통화 KRW
    품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액
    스테인리스 브라켓 BRK-SUS-01 50x80x3T 100 EA 1500 150000 15000 165000
    총액 165,000
    """
    document = Document(
        original_filename="quote.pdf",
        stored_file_path=str(path),
        mime_type="application/pdf",
        processing_status=ProcessingStatus.uploaded,
    )
    processor = _processor(
        FakeVLWorker(
            {
                "ok": True,
                "provider": "paddleocr_vl_1_6_gguf",
                "classification": "pass",
                "text": text,
                "validation": {"status": "pass", "ok": True},
            }
        )
    )

    class BrokenIngestion:
        def ingest(self, *args, **kwargs):
            raise AssertionError("PP-OCRv4 ingestion should be skipped when VL primary promotes")

    processor.ingestion = BrokenIngestion()

    result = processor.process(FakeSession(document), document)

    assert result.processing_status in {ProcessingStatus.ready, ProcessingStatus.needs_review}
    assert result.extraction_method == "paddleocr_vl_1_6_gguf_primary_reader"
    assert result.document_number == "QT-2026-0808-010"
    assert result.extracted_amount == Decimal("165000")
    assert len(result.line_items or []) == 1
    assert result.workflow_metadata["vl_candidate_summary"]["promotion_applied"] is True
