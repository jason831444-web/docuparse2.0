import sys
from datetime import date
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
from app.services.parser import ParsedDocument


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


def test_vl_upload_pipeline_suppresses_mismatched_amounts_during_promotion():
    document = _document(document_type=DocumentType.transaction_statement)
    structured = {
        "document": {"document_type": "transaction_statement", "document_number": "TS-GEN-2026-008"},
        "line_items": [
            {
                "item_name": "SUS304 3T PLATE",
                "quantity": 3,
                "unit": "EA",
                "unit_price": 35000,
                "supply_amount": 10,
                "validation_warnings": ["explicit_quantity_price_amount_mismatch"],
            }
        ],
    }

    DocumentProcessor()._apply_vl_structured_candidate(document, structured)

    assert len(document.line_items or []) == 1
    assert document.line_items[0]["quantity"] == 3
    assert document.line_items[0]["unit_price"] == 35000
    assert "supply_amount" not in document.line_items[0]
    assert "line_total" not in document.line_items[0]
    assert "vl_amount_suppressed_due_to_arithmetic_mismatch" in document.line_items[0]["validation_warnings"]


def test_vl_upload_pipeline_suppresses_mismatched_amounts_at_final_assignment_boundary():
    line_items = [
        {
            "item_name": "SUS304 3T PLATE",
            "quantity": 3,
            "unit_price": 35000,
            "supply_amount": 10,
            "tax_amount": 1,
            "line_total": 11,
            "validation_warnings": ["explicit_quantity_price_amount_mismatch"],
        }
    ]

    safe_items = DocumentProcessor()._line_items_for_extraction_method(
        line_items,
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert safe_items[0]["quantity"] == 3
    assert safe_items[0]["unit_price"] == 35000
    assert "supply_amount" not in safe_items[0]
    assert "tax_amount" not in safe_items[0]
    assert "line_total" not in safe_items[0]
    assert "vl_amount_suppressed_due_to_arithmetic_mismatch" in safe_items[0]["review_flags"]


def test_vl_promoted_candidate_overrides_reparsed_vl_text_before_item_matching():
    processor = DocumentProcessor()
    parsed = ParsedDocument(
        document_type=DocumentType.transaction_statement,
        document_number="TS-GEN-2026-008",
        extracted_amount=Decimal("705100"),
        currency="KRW",
        line_items=[
            {
                "item_name": "SUS304 3T PLATE",
                "quantity": 1,
                "unit": "EA",
                "unit_price": 35000,
                "supply_amount": 35000,
            }
        ],
    )
    structured = {
        "document": {
            "document_type": "transaction_statement",
            "document_number": "TS-GEN-2026-008",
            "currency": "KRW",
            "total": "705100",
        },
        "line_items": [
            {
                "item_name": "SUS304 3T PLATE",
                "quantity": 3,
                "unit": "EA",
                "unit_price": 35000,
                "validation_warnings": ["missing_line_amount", "row_amount_hidden_do_not_infer"],
            }
        ],
    }

    processor._apply_vl_structured_candidate_to_parsed(parsed, structured)

    assert parsed.document_number == "TS-GEN-2026-008"
    assert parsed.extracted_amount == Decimal("705100")
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["quantity"] == 3
    assert parsed.line_items[0]["unit_price"] == 35000
    assert "supply_amount" not in parsed.line_items[0]
    assert "row_amount_hidden_do_not_infer" in parsed.line_items[0]["validation_warnings"]


def test_vl_structured_candidate_does_not_overwrite_distinct_invoice_issue_date_with_due_date():
    processor = DocumentProcessor()
    parsed = ParsedDocument(
        document_type=DocumentType.invoice,
        document_number="INV-VIS-2026-003-ROUND",
        issue_date=date(2026, 11, 3),
        extracted_date=date(2026, 11, 3),
        due_date=date(2026, 12, 3),
        line_items=[],
    )
    structured = {
        "document": {
            "document_type": "invoice",
            "document_number": "INV-VIS-2026-003-ROUND",
            "issue_date": "2026-12-03",
            "due_date": "2026-12-03",
        },
        "line_items": [],
    }

    processor._apply_vl_structured_candidate_to_parsed(parsed, structured)

    assert parsed.issue_date == date(2026, 11, 3)
    assert parsed.extracted_date == date(2026, 11, 3)
    assert parsed.due_date == date(2026, 12, 3)


def test_vl_structured_candidate_does_not_overwrite_parser_header_fields_with_table_header_noise():
    processor = DocumentProcessor()
    parsed = ParsedDocument(
        document_type=DocumentType.invoice,
        document_number="INV-US-GEN-004",
        vendor_name="Global Motion Parts LLC",
        customer_name="NeoFactory Korea",
        currency="USD",
        line_items=[],
    )
    structured = {
        "document": {
            "document_type": "invoice",
            "document_number": None,
            "vendor_name": "SKU Spec Qty Unit Unit Price A",
            "customer_name": None,
            "currency": "USD",
        },
        "line_items": [
            {
                "item_name": "Linear Guide Rail HGW20",
                "document_item_code": "HGW20-1000",
                "quantity": 10,
                "unit": "EA",
                "unit_price": 45,
                "validation_warnings": ["missing_line_amount"],
            }
        ],
    }

    processor._apply_vl_structured_candidate_to_parsed(parsed, structured)

    assert parsed.document_number == "INV-US-GEN-004"
    assert parsed.vendor_name == "Global Motion Parts LLC"
    assert parsed.customer_name == "NeoFactory Korea"
    assert parsed.currency == "USD"
    assert parsed.line_items[0]["item_name"] == "Linear Guide Rail HGW20"


def test_vl_upload_pipeline_does_not_promote_negative_document_level_amounts():
    document = _document(document_type=DocumentType.general_document)
    structured = {
        "document": {
            "document_type": "general_document",
            "document_number": "RTN-GEN-2026-006",
            "currency": "KRW",
            "subtotal": "-3",
            "tax": None,
            "total": "12100",
        },
        "line_items": [],
    }

    DocumentProcessor()._apply_vl_structured_candidate(document, structured)

    assert document.document_number == "RTN-GEN-2026-006"
    assert document.currency == "KRW"
    assert document.subtotal is None
    assert document.extracted_amount == Decimal("12100")


def test_document_processor_suppresses_negative_document_level_amount_boundary():
    processor = DocumentProcessor()

    assert processor._nonnegative_document_amount(Decimal("-3")) is None
    assert processor._nonnegative_document_amount(Decimal("0")) == Decimal("0")
    assert processor._nonnegative_document_amount(Decimal("12100")) == Decimal("12100")


def test_document_processor_normalizes_internal_transfer_broad_type_boundary():
    parsed = SimpleNamespace(document_type=DocumentType.other)

    assert DocumentProcessor()._internal_transfer_document_type(parsed) == DocumentType.general_document


def test_process_keeps_vl_internal_transfer_as_no_price_general_document(tmp_path):
    path = tmp_path / "transfer.pdf"
    path.write_bytes(b"%PDF-1.4\n% fake test file not read when VL succeeds\n")
    text = """
    사업장간 자재 이동 요청서
    문서번호 TRF-GEN-2026-005
    출고창고 1공장 원자재창고 입고창고 2공장 생산라인
    No 품목명 내부품목코드 규격 요청수량 단위
    1 SUS304 2T PLATE M-PLT-SUS304-2T-1000X2000 1000x2000 2 EA
    2 M8 육각 볼트 P-BOLT-M8-20-ZN M8x20 500 EA
    금액 없는 내부 이동 문서
    """
    document = Document(
        original_filename="transfer.pdf",
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
            raise AssertionError("PP-OCRv4 ingestion should be skipped for safe internal transfer VL promotion")

    processor.ingestion = BrokenIngestion()

    result = processor.process(FakeSession(document), document)

    assert result.extraction_method == "paddleocr_vl_1_6_gguf_primary_reader"
    assert result.document_type == DocumentType.general_document
    assert result.document_number == "TRF-GEN-2026-005"
    assert result.extracted_amount is None
    assert result.currency is None
    assert result.category == "internal_transfer"
    assert "internal_transfer" in result.tags
    assert result.workflow_metadata["document_subtype"] == "internal_transfer"
    assert result.workflow_metadata["document_profile"] == "inventory_movement_document"
    assert "no_price_document" in result.workflow_metadata["document_profiles"]
    assert len(result.line_items or []) == 2
    assert result.line_items[0]["quantity"] == 2
    assert result.line_items[0]["requested_quantity"] == 2
    assert "supply_amount" not in result.line_items[0]


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
