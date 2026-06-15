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
from app.services.file_ingestion import NormalizedDocument


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


def test_vl_upload_pipeline_partially_promotes_blank_quantity_candidate():
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
    document = _document()

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is not None
    assert metadata["vl_candidate_summary"]["promotion_applied"] is True
    assert metadata["vl_candidate_summary"]["promotion_mode"] == "partial"
    assert metadata["vl_candidate_summary"]["partial_promotion_applied"] is True
    assert metadata["vl_candidate_summary"]["fallback_used"] is False
    assert metadata["vl_candidate_summary"]["requires_review"] is True
    assert metadata["vl_candidate_summary"]["gate_decision"] == "review_required"
    assert metadata["vl_candidates"][0]["candidate_only"] is False
    assert metadata["vl_candidates"][0]["parser_integrated"] is True
    assert document.document_number == "QT-2026-0808-009"
    assert document.currency == "KRW"
    assert document.extracted_amount == Decimal("473000")
    assert len(document.line_items or []) == 2
    assert document.line_items[0]["item_name"] == "고정 플레이트"
    assert document.line_items[0].get("quantity") is None
    assert document.line_items[0]["unit_price"] == 2800
    assert document.line_items[0]["supply_amount"] == 280000
    assert document.line_items[0]["tax_amount"] == 28000
    assert document.line_items[0]["line_total"] == 308000
    assert "missing_quantity" in document.line_items[0]["validation_warnings"]
    assert document.line_items[1]["quantity"] == 100
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


def test_vl_upload_pipeline_records_fallback_when_worker_times_out_without_text():
    worker = FakeVLWorker(
        {
            "ok": False,
            "provider": "paddleocr_vl_1_6_gguf",
            "status": "failed",
            "fallback_reason": "ReadTimeout: read timeout=240.0",
            "elapsed_ms": 240000,
        }
    )
    document = _document()

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is not None
    summary = metadata["vl_candidate_summary"]
    assert summary["candidate_count"] == 0
    assert summary["promotion_applied"] is False
    assert summary["parser_integrated"] is False
    assert summary["fallback_used"] is True
    assert summary["fallback_reason"] == "ReadTimeout: read timeout=240.0"
    assert summary["failure_count"] == 1


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


def test_process_uses_partial_vl_primary_and_skips_ppocr_ingestion_for_review_candidate(tmp_path):
    path = tmp_path / "quote-missing-quantity.pdf"
    path.write_bytes(b"%PDF-1.4\n% fake test file not read when VL succeeds with review warnings\n")
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
    document = Document(
        original_filename="quote-missing-quantity.pdf",
        stored_file_path=str(path),
        mime_type="application/pdf",
        processing_status=ProcessingStatus.uploaded,
    )
    processor = _processor(
        FakeVLWorker(
            {
                "ok": True,
                "provider": "paddleocr_vl_1_6_gguf",
                "classification": "warn",
                "text": text,
                "validation": {"status": "warn", "ok": False},
            }
        )
    )

    class BrokenIngestion:
        def ingest(self, *args, **kwargs):
            raise AssertionError("PP-OCRv4 ingestion should be skipped for partial VL primary promotion")

    processor.ingestion = BrokenIngestion()

    result = processor.process(FakeSession(document), document)

    assert result.processing_status == ProcessingStatus.needs_review
    assert result.extraction_method == "paddleocr_vl_1_6_gguf_primary_reader"
    assert result.document_number == "QT-2026-0808-009"
    assert result.extracted_amount == Decimal("473000")
    assert len(result.line_items or []) == 2
    assert result.line_items[0].get("quantity") is None
    assert "missing_quantity" in result.line_items[0]["validation_warnings"]
    summary = result.workflow_metadata["vl_candidate_summary"]
    assert summary["promotion_applied"] is True
    assert summary["promotion_mode"] == "partial"
    assert summary["partial_promotion_applied"] is True
    assert summary["fallback_used"] is False


def test_process_falls_back_to_ingestion_when_vl_candidate_has_unrepaired_invalid_amounts(tmp_path):
    path = tmp_path / "po-text-layer.pdf"
    path.write_bytes(b"%PDF-1.4\n% fake test file read by fallback ingestion\n")
    vl_text = """
    발주서
    발주번호 PO-2026-0911-104
    품목명 규격 수량 단위 단가 공급가액
    SUS304 2T PLATE 1000x2000 6 EA 25000 150000 1
    합계금액 4
    """
    fallback_text = """
    발주서
    발주번호 PO-2026-0911-104
    발주일 2026-09-11
    품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액
    SUS304 2T PLATE STS304-2T 1000x2000 6 EA 25000 150000 15000 165000
    M8 육각볼트 BOLT-M8-20 M8x20 1500 EA 120 180000 18000 198000
    SUS WASHER M8 WASH-M8 M8 500 EA 60 30000 3000 33000
    고정 플레이트 FIX-PLT-120 120x60x5T 40 EA 5000 200000 20000 220000
    합계금액 616000
    """
    document = Document(
        original_filename="po-text-layer.pdf",
        stored_file_path=str(path),
        mime_type="application/pdf",
        processing_status=ProcessingStatus.uploaded,
    )
    processor = _processor(
        FakeVLWorker(
            {
                "ok": True,
                "provider": "paddleocr_vl_1_6_gguf",
                "classification": "warn",
                "text": vl_text,
                "validation": {"status": "warn", "ok": False},
            }
        )
    )

    class FallbackIngestion:
        called = False

        def ingest(self, *args, **kwargs):
            self.called = True
            return NormalizedDocument(
                source_file_type="pdf",
                mime_type="application/pdf",
                extraction_method="pdf_text_extract",
                normalized_text=fallback_text,
                raw_extracted_blocks=[{"type": "pdf_text", "content": fallback_text}],
                extraction_warnings=[],
                file_metadata={"text_layer_exists": True},
            )

    ingestion = FallbackIngestion()
    processor.ingestion = ingestion

    result = processor.process(FakeSession(document), document)

    assert ingestion.called is True
    assert result.extraction_method == "pdf_text_extract"
    assert result.document_number == "PO-2026-0911-104"
    assert len(result.line_items or []) == 4
    summary = result.workflow_metadata["vl_candidate_summary"]
    assert summary["promotion_applied"] is False
    assert summary["promotion_mode"] == "none"
    assert summary["fallback_used"] is True
    assert "vl_candidate_missing_line_amount" in summary["issue_codes"]
