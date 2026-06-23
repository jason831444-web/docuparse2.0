import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

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


def _write_test_image(path: Path, *, color: tuple[int, int, int] = (235, 235, 232)) -> None:
    Image.new("RGB", (320, 420), color).save(path)


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


def test_final_business_safety_removes_standalone_receipt_summary_rows():
    document = _document(
        document_type=DocumentType.receipt,
        line_items=[
            {"item_name": "절삭유", "quantity": 2, "line_total": 38000},
            {"item_name": "공급가액", "quantity": "68,636"},
            {"item_name": "부가세", "quantity": "6,864"},
            {"item_name": "합계", "quantity": "75,500"},
        ],
    )

    issues = _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "영수증\n품목 수량 금액\n절삭유 2 38,000\n공급가액 68,636\n부가세 6,864\n합계 75,500",
    )

    assert [item["item_name"] for item in document.line_items] == ["절삭유"]
    assert any(issue["code"] == "summary_total_not_line_item" for issue in issues)


def test_final_business_safety_removes_date_fragment_total_amount():
    document = _document(
        document_type=DocumentType.receipt,
        extracted_amount=Decimal("2026.06"),
        subtotal=Decimal("68636"),
        tax=Decimal("6864"),
        line_items=[{"item_name": "절삭유", "quantity": 2, "line_total": 38000}],
    )

    issues = _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "거래일시 2026.06.13 14:22\n합계 75,500",
    )

    assert document.extracted_amount is None
    assert document.subtotal == Decimal("68636")
    assert document.tax == Decimal("6864")
    assert any(issue["code"] == "date_fragment_not_total_amount" for issue in issues)


def test_ai_parsed_store_candidate_fills_receipt_vendor_without_guessing_customer():
    document = _document(document_type=DocumentType.receipt, vendor_name=None, customer_name=None, merchant_name=None)
    metadata = {}
    ai_parsed = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "매장",
                        "value": "시흥공구마트",
                        "normalized_key": "customer_name",
                        "evidence": "매장 시흥공구마트",
                        "status": "candidate",
                    }
                ],
            }
        ]
    }

    _processor(FakeVLWorker())._apply_ai_parsed_document_candidates(
        document,
        ai_parsed,
        metadata,
        "영수증\n매장 시흥공구마트",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.vendor_name == "시흥공구마트"
    assert document.merchant_name == "시흥공구마트"
    assert document.customer_name is None
    assert metadata["ai_parsed_document_mapping"]["applied_fields"].count("vendor_name") == 1
    assert metadata["ai_parsed_document_mapping"]["applied_fields"].count("merchant_name") == 1


def test_ai_parsed_store_candidate_fills_pos_vendor_from_settlement_signal():
    document = _document(document_type=DocumentType.general_document, vendor_name=None, customer_name=None, merchant_name=None)
    metadata = {}
    ai_parsed = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "매장",
                        "value": "대야역 분식",
                        "normalized_key": "customer_name",
                        "evidence": "매장 대야역 분식",
                        "status": "candidate",
                    }
                ],
            }
        ]
    }

    _processor(FakeVLWorker())._apply_ai_parsed_document_candidates(
        document,
        ai_parsed,
        metadata,
        "POS 일정산\n매장 대야역 분식\n순 판매 금액 1,060,000",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.vendor_name == "대야역 분식"
    assert document.merchant_name == "대야역 분식"
    assert document.customer_name is None


def test_ai_parsed_store_candidate_does_not_fill_party_without_pos_signal():
    document = _document(document_type=DocumentType.general_document, vendor_name=None, customer_name=None, merchant_name=None)
    metadata = {}
    ai_parsed = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "Store",
                        "value": "A-01 원자재",
                        "normalized_key": "customer_name",
                        "evidence": "Store A-01 원자재",
                        "status": "candidate",
                    }
                ],
            }
        ]
    }

    _processor(FakeVLWorker())._apply_ai_parsed_document_candidates(
        document,
        ai_parsed,
        metadata,
        "자재 이동 요청서\nStore A-01 원자재",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    mapping = metadata["ai_parsed_document_mapping"]
    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None
    assert not mapping["applied_fields"]
    assert mapping["review_only_fields"][0]["reason"] == "store_candidate_without_pos_or_receipt_context"


def test_ai_parsed_warehouse_candidates_do_not_fill_vendor_or_customer():
    document = _document(document_type=DocumentType.general_document, vendor_name=None, customer_name=None)
    metadata = {}
    ai_parsed = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "출고창고",
                        "value": "A-01 원자재",
                        "normalized_key": "source_warehouse",
                        "evidence": "출고창고 A-01 원자재",
                        "status": "candidate",
                    },
                    {
                        "key": "입고창고",
                        "value": "B-02 가공",
                        "normalized_key": "destination_warehouse",
                        "evidence": "입고창고 B-02 가공",
                        "status": "candidate",
                    },
                ],
            }
        ]
    }

    _processor(FakeVLWorker())._apply_ai_parsed_document_candidates(
        document,
        ai_parsed,
        metadata,
        "자재 이동 요청서\n출고창고 A-01 원자재\n입고창고 B-02 가공",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.vendor_name is None
    assert document.customer_name is None
    assert "ai_parsed_document_mapping" not in metadata


def test_ai_parsed_review_only_party_candidate_does_not_fill_confirmed_fields():
    document = _document(document_type=DocumentType.delivery_note, vendor_name=None, customer_name=None, merchant_name=None)
    metadata = {}
    ai_parsed = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "상단 거래처 후보",
                        "value": "대한유통",
                        "normalized_key": "party_name",
                        "evidence": "대한유통",
                        "status": "review_only",
                        "confidence": 0.48,
                    }
                ],
            }
        ]
    }

    _processor(FakeVLWorker())._apply_ai_parsed_document_candidates(
        document,
        ai_parsed,
        metadata,
        "대한유통\n납품서\n문서번호 DN-2026-0003",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    mapping = metadata["ai_parsed_document_mapping"]
    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None
    assert mapping["review_only_fields"][0]["reason"] == "ai_parsed_document_review_only"


def test_final_party_safety_removes_document_numbers_and_upload_paths():
    document = _document(
        document_type=DocumentType.receipt,
        vendor_name="DOC-O41",
        customer_name="/workspace/docuparse-gpu-test/uploads/vl_rendered_pages/abc-DOC-065.png",
        merchant_name="IDOC-026",
    )

    _processor(FakeVLWorker())._normalize_party_fields(document)

    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None


def test_vl_raw_text_cleaner_removes_generated_upload_path_lines():
    processor = _processor(FakeVLWorker())

    cleaned = processor._clean_vl_raw_text(
        "/workspace/docuparse-gpu-test/uploads/vl_remote_uploads/abc-DOC-001.jpg\n"
        "발주서\n문서번호 PO-2026-0001\n"
        "/workspace/docuparse-gpu-test/uploads/vl_rendered_pages/abc-page-1.png\n"
        "대성정공"
    )

    assert "/workspace/" not in cleaned
    assert cleaned.splitlines() == ["발주서", "문서번호 PO-2026-0001", "대성정공"]


def test_vl_raw_text_cleaner_splits_literal_newline_sequences():
    processor = _processor(FakeVLWorker())

    cleaned = processor._clean_vl_raw_text("세금계산서\\n공급자\\n상호: (주)삼광유통\\n공급받는자\\n상호: (주)신우정밀")

    assert cleaned.splitlines() == ["세금계산서", "공급자", "상호: (주)삼광유통", "공급받는자", "상호: (주)신우정밀"]


def test_party_sanitizer_removes_address_phone_and_warehouse_values():
    document = _document(
        document_type=DocumentType.general_document,
        vendor_name="경기도 시흥시 공단로 18 | 031-555-1290",
        customer_name="출고창고 A-01 원자재",
        merchant_name="66.92.198.186:11059",
    )

    _processor(FakeVLWorker())._normalize_party_fields(document)

    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None


def test_party_sanitizer_blocks_table_headers_and_document_number_fragments():
    document = _document(
        document_type=DocumentType.invoice,
        vendor_name="SKU Spec Qty Unit Unit Price A",
        customer_name="INV-US-GEN- OO4",
        merchant_name="Vendor SKU Spec Qty",
    )

    _processor(FakeVLWorker())._normalize_party_fields(document)

    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None


def test_party_sanitizer_blocks_document_titles_and_option_terms():
    document = _document(
        document_type=DocumentType.general_document,
        vendor_name="Internal Transfer",
        customer_name="옵션 긴급 납품 옵션 FAST-DELIVERY 별도협의",
        merchant_name="Tax Invoice",
    )

    _processor(FakeVLWorker())._normalize_party_fields(document)

    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.merchant_name is None


def test_receipt_candidates_do_not_trigger_from_item_name_in_manufacturing_document():
    processor = _processor(FakeVLWorker())
    document = _document(document_type=DocumentType.inspection_report, category="inspection_report", tags=["inspection_report"])
    raw_text = """
입고 검사 기록서
No 품명 Lot/Code 입고수량 검사항목 판정 비고
1 POS 영수증 용지 POS-PAPER 50 외관/치수 재검 스크래치 확인
"""

    assert processor._receipt_context_signal(document, raw_text) is False
    assert processor._receipt_review_row_candidates(document, raw_text, raw_text) == []


def test_return_credit_signal_requires_document_level_signal_not_delivery_note_exclusion_note():
    processor = _processor(FakeVLWorker())
    document = _document(document_type=DocumentType.delivery_note, category="delivery_note", tags=["delivery_note"], document_number="DN-2026-0003")

    assert processor._return_credit_signal_for_document(
        document,
        "납품서\n문서번호 DN-2026-0003\n비고 반품 2박스 제외\nS45C PIN 120 EA",
    ) is False
    assert processor._return_credit_signal_for_document(
        document,
        "반품/크레딧 메모\n문서번호 RCM-2026-0009\n원문서 TS-2026-0034\n사유 규격 불일치\n-36,000",
    ) is True


def test_supplier_customer_block_promotes_labeled_party_candidates():
    document = _document(document_type=DocumentType.invoice, vendor_name=None, customer_name=None)

    _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "세금계산서\n공급자\n상호: (주)삼광유동\n사업자번호 123-45-67890\n"
        "공급받는자\n상호: (주)신우정밀\n품목 수량 단가",
    )

    assert document.vendor_name == "삼광유동"
    assert document.customer_name == "신우정밀"
    assert document.field_sources["vendor_name"] == "raw_text_party_block"
    assert document.field_sources["customer_name"] == "raw_text_party_block"


def test_trade_partner_label_is_preserved_as_customer_review_candidate():
    processor = _processor(FakeVLWorker())
    document = _document(document_type=DocumentType.delivery_note, vendor_name="대성정공", customer_name=None, merchant_name="대성정공")
    workflow_metadata = {}
    ai_doc = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {
                        "key": "거래처",
                        "value": "대성정공",
                        "normalized_key": "customer_name",
                        "evidence": "거래처 대성정공",
                        "confidence": 0.78,
                        "status": "candidate",
                    }
                ],
            }
        ],
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "납품서\n거래처 대성정공\n단가 미기재 납품서",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.customer_name is None
    candidates = workflow_metadata["party_review_candidates"]
    assert candidates == [
        {
            "field": "customer_name",
            "role": "customer",
            "value": "대성정공",
            "normalized_value": "대성정공",
            "source": "vl_raw_text",
            "source_label": "거래처",
            "evidence": "거래처 대성정공",
            "confidence": 0.78,
            "status": "review_only",
            "reason": "party_candidate_review_required",
        }
    ]


def test_return_credit_reference_number_is_not_document_number_candidate():
    processor = _processor(FakeVLWorker())
    document = _document(document_type=DocumentType.general_document, document_number="RCM-2026-0009", customer_name=None)
    workflow_metadata = {}
    ai_doc = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {"key": "거래처", "value": "신우금속", "normalized_key": "customer_name", "evidence": "거래처 신우금속"},
                    {"key": "원문서", "value": "TS-2026-0034", "normalized_key": "reference_document_number", "evidence": "원문서 TS-2026-0034"},
                ],
            }
        ]
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "반품/크레딧 메모\n문서번호 RCM-2026-0009\n거래처 신우금속\n원문서 TS-2026-0034",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.document_number == "RCM-2026-0009"
    assert document.customer_name is None
    assert workflow_metadata["party_review_candidates"][0]["role"] == "customer"
    assert workflow_metadata["document_number_candidates"][0]["field"] == "reference_document_number"
    assert workflow_metadata["document_number_candidates"][0]["normalized_value"] == "TS-2026-0034"


def test_filename_doc_number_guard_corrects_conflicting_doc_header_with_sample_number():
    document = _document(
        original_filename="DOC-028_return_credit_uncropped_photo.pdf",
        document_type=DocumentType.general_document,
        document_number="DOC-078",
    )
    processor = DocumentProcessor()

    issues = processor._apply_final_business_safety_overrides(
        document,
        "문서번호:DOC-078\n반품/크레딧 메모\n샘플번호:028",
    )

    assert document.document_number == "DOC-028"
    assert document.field_sources["document_number"] == "filename_doc_number_consistency_guard"
    assert "document_number_filename_mismatch_corrected" in {issue["code"] for issue in issues}


def test_receipt_approval_number_is_kept_separate_from_document_number():
    processor = _processor(FakeVLWorker())
    document = _document(document_type=DocumentType.receipt, document_number=None)
    workflow_metadata = {}
    ai_doc = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {"key": "승인번호", "value": "RC-2026-0029", "normalized_key": "approval_number", "evidence": "승인번호 RC-2026-0029"},
                ],
            }
        ]
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "영수증\n승인번호 RC-2026-0029\n카드 결제",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.document_number is None
    assert workflow_metadata["document_number_candidates"][0]["field"] == "approval_number"


def test_pos_safety_branch_still_removes_identifier_party_values():
    document = _document(
        document_type=DocumentType.receipt,
        vendor_name="IDOC-026",
        merchant_name="IDOC-026",
        line_items=[
            {"item_name": "순 판매 금액", "line_total": 1000},
            {"item_name": "카드 합계", "line_total": 900},
            {"item_name": "주문 횟수", "quantity": 3},
        ],
    )

    _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "POS 일정산\n영수증번호 IDOC-026\n순 판매 금액 1,000\n카드 합계 900\n주문 횟수 3",
    )

    assert document.category == "pos_daily_settlement"
    assert document.vendor_name is None
    assert document.merchant_name is None


def test_item_master_skip_reason_for_pos_and_receipt_documents():
    processor = _processor(FakeVLWorker())
    pos_doc = _document(
        document_type=DocumentType.general_document,
        title="일정산",
        category="pos_daily_settlement",
        tags=["pos_daily_settlement"],
        line_items=[{"item_name": "순 판매 금액", "line_total": 1000}],
    )
    receipt_doc = _document(document_type=DocumentType.receipt, line_items=[{"item_name": "절삭유", "quantity": 2}])

    assert processor._item_master_skip_reason(pos_doc, "POS 일정산\n순 판매 금액 1,000") == "pos_daily_settlement_not_manufacturing_item_document"
    assert processor._item_master_skip_reason(receipt_doc, "영수증\n절삭유 2 38,000") == "receipt_not_manufacturing_item_document"


def test_receipt_top_line_promotes_merchant_and_vendor_candidate():
    document = _document(document_type=DocumentType.receipt, vendor_name=None, merchant_name=None)

    _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "가온마트\n영수증번호:\nDOC-041\n일자:\n20260612\n양파 15kg 2BOX",
    )

    assert document.vendor_name == "가온마트"
    assert document.merchant_name == "가온마트"
    assert document.field_sources["vendor_name"] == "receipt_top_line_candidate"
    assert document.field_sources["merchant_name"] == "receipt_top_line_candidate"


def test_receipt_top_line_skips_paths_and_identifier_lines():
    document = _document(document_type=DocumentType.receipt, vendor_name=None, merchant_name=None)

    _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "/workspace/docuparse-gpu-test/uploads/vl_rendered_pages/abc-DOC-065.png\n가온마트\n영수증번호:DOC-065\n일자:2026.06.15",
    )

    assert document.vendor_name == "가온마트"
    assert document.merchant_name == "가온마트"


def test_receipt_top_line_does_not_promote_general_document_without_receipt_signal():
    document = _document(document_type=DocumentType.general_document, vendor_name=None, merchant_name=None)

    _processor(FakeVLWorker())._apply_final_business_safety_overrides(
        document,
        "가온마트\n문서번호 DOC-001\n납품서\nNo 품목 수량",
    )

    assert document.vendor_name is None
    assert document.merchant_name is None


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


def test_vl_normalized_document_skips_header_ocr_when_structured_document_number_exists(tmp_path):
    processor = DocumentProcessor()
    page_image = tmp_path / "page.png"
    _write_test_image(page_image)
    processor._document_quality_for_source = lambda stored_path: ({}, [page_image])
    processor.ocr.extract = lambda image_path: (_ for _ in ()).throw(AssertionError("header OCR should be skipped"))
    metadata = {
        "vl_provider_metadata": {"elapsed_ms": 1200},
        "vl_candidates": [
            {
                "structured_candidate": {
                    "document": {"document_number": "INV-US-GEN-004"},
                    "line_items": [],
                },
                "parser_evaluated": True,
            }
        ],
    }
    document = _document(original_filename="hidden-invoice.pdf", mime_type="application/pdf")

    normalized = processor._vl_primary_normalized_document(
        tmp_path / "hidden-invoice.pdf",
        document,
        "Commercial Invoice\nVendor Global Motion Parts LLC",
        metadata,
    )

    assert normalized.file_metadata["header_ocr_supplement_used"] is False
    assert normalized.file_metadata["header_ocr_supplement_skipped_reason"] == "structured_candidate_document_number_found"
    assert metadata["header_ocr_supplement"]["used"] is False
    assert metadata["header_ocr_supplement"]["skipped_reason"] == "structured_candidate_document_number_found"
    assert metadata["header_ocr_supplement"]["elapsed_ms"] >= 0


def test_vl_normalized_document_records_header_ocr_supplement_timing_when_used(tmp_path):
    processor = DocumentProcessor()
    page_image = tmp_path / "page.png"
    _write_test_image(page_image)
    processor._document_quality_for_source = lambda stored_path: ({}, [page_image])
    processor.ocr.extract = lambda image_path: SimpleNamespace(
        text="문서번호 INV-2026-0001\n발행일 2026.01.01\n품목명 규격 수량"
    )
    metadata = {"vl_provider_metadata": {"elapsed_ms": 1200}, "vl_candidates": []}
    document = _document(original_filename="invoice.pdf", mime_type="application/pdf")

    normalized = processor._vl_primary_normalized_document(
        tmp_path / "invoice.pdf",
        document,
        "Commercial Invoice\nVendor Global Motion Parts LLC",
        metadata,
    )

    assert normalized.file_metadata["header_ocr_supplement_used"] is True
    assert normalized.file_metadata["header_ocr_supplement_reason"] == "document_number_missing_in_vl_text_and_structured_candidate"
    assert normalized.file_metadata["header_ocr_supplement_ms"] >= 0
    assert metadata["header_ocr_supplement"]["used"] is True
    assert metadata["header_ocr_supplement"]["elapsed_ms"] >= 0
    assert normalized.normalized_text.startswith("문서번호 INV-2026-0001")


def test_vl_normalized_document_skips_header_ocr_with_ai_parsed_document_candidate(tmp_path):
    processor = DocumentProcessor()
    page_image = tmp_path / "page.png"
    _write_test_image(page_image)
    processor._document_quality_for_source = lambda stored_path: ({}, [page_image])
    processor.ocr.extract = lambda image_path: (_ for _ in ()).throw(AssertionError("header OCR should be skipped"))
    metadata = {"vl_provider_metadata": {"elapsed_ms": 1200}, "vl_candidates": []}
    document = _document(original_filename="transaction-statement.pdf", mime_type="application/pdf")

    normalized = processor._vl_primary_normalized_document(
        tmp_path / "transaction-statement.pdf",
        document,
        "거래명세서\n문서번호 TS-2026-0008\n거래처 대성정공",
        metadata,
    )

    assert normalized.file_metadata["header_ocr_supplement_used"] is False
    assert normalized.file_metadata["header_ocr_supplement_skipped_reason"] == "raw_text_document_number_found"
    assert metadata["header_ocr_supplement"]["used"] is False


def test_vl_normalized_document_skips_header_ocr_when_document_policy_optional(tmp_path):
    processor = DocumentProcessor()
    page_image = tmp_path / "page.png"
    _write_test_image(page_image)
    processor._document_quality_for_source = lambda stored_path: ({}, [page_image])
    processor.ocr.extract = lambda image_path: (_ for _ in ()).throw(AssertionError("header OCR should be skipped"))
    metadata = {"vl_provider_metadata": {"elapsed_ms": 1200}, "vl_candidates": []}
    document = _document(original_filename="receipt.jpg", mime_type="image/jpeg")

    normalized = processor._vl_primary_normalized_document(
        tmp_path / "receipt.jpg",
        document,
        "영수증\n거래일시 2026.06.13 14:22\n시흥공구마트",
        metadata,
    )

    assert normalized.file_metadata["header_ocr_supplement_used"] is False
    assert normalized.file_metadata["header_ocr_supplement_skipped_reason"] == "document_type_policy_document_number_optional"
    assert metadata["header_ocr_supplement"]["policy_decision"]["policy"] == "ai_parsed_document"


def test_vl_upload_pipeline_promotes_visible_official_table_amounts():
    text = """
    세금계산서
    문서번호 INV-2026-0002
    작성일자 2026.06.12
    공급가액 합계 729,000 세액 합계 72,900 청구금액 801,900
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "pass",
            "text": text,
            "elapsed_ms": 12000,
            "validation": {"status": "pass", "ok": True},
            "schema_prompt": {
                "used": True,
                "official_table_count": 1,
                "table_source": "paddleocrvl_official_table_html",
            },
            "tables": [
                {
                    "table_type": "line_items",
                    "source": "paddleocrvl_official_table_html",
                    "columns": ["품목", "규격", "수량", "단가", "공급가액", "세액", "합계"],
                    "rows": [
                        {
                            "item_name": "PCB Connector",
                            "specification": "12P",
                            "quantity": 200,
                            "unit_price": 1250,
                            "supply_amount": 250000,
                            "tax_amount": 25000,
                            "line_total": 275000,
                        },
                        {
                            "item_name": "Cable Harness",
                            "specification": "500mm",
                            "quantity": 80,
                            "unit_price": 2800,
                            "supply_amount": 224000,
                            "tax_amount": 22400,
                            "line_total": 246400,
                        },
                    ],
                    "warnings": ["paddleocrvl_official_table_review_required"],
                    "review_required": True,
                }
            ],
        }
    )
    document = _document(
        original_filename="MFG-002_tax_invoice_uncropped.png",
        document_type=DocumentType.invoice,
    )

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is not None
    assert metadata["vl_provider_metadata"]["schema_prompt"]["official_table_count"] == 1
    assert len(document.line_items or []) == 2
    first = document.line_items[0]
    assert first["item_name"] == "PCB Connector"
    assert first["quantity"] == 200
    assert first["unit_price"] == 1250
    assert first["supply_amount"] == 250000
    assert first["tax_amount"] == 25000
    assert first["line_total"] == 275000


def test_vl_upload_pipeline_uses_original_image_by_default(tmp_path):
    text = """
    자재 이동 요청서
    문서번호 MV-2026-0010
    요청일 2026.06.18
    No 품목 규격 수량 단위 이동사유
    1 S45C PIN 8X60 200 EA 2라인 긴급 투입
    2 AL6061 환봉 10파이 50 EA 가공 대기
    3 절삭유 4L 6 CAN 공용 소모품
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "pass",
            "text": text,
            "validation": {"status": "pass", "ok": True},
        }
    )
    processor = _processor(worker)
    source_path = tmp_path / "DOC-010_internal_transfer_blurry_uncropped_photo.webp"
    _write_test_image(source_path)
    processor._safe_quality_for_vl_input = lambda _path: {
        "likely_scan_type": "scan",
        "overall_quality_score": 0.9,
        "possible_right_column_crop": False,
        "hidden_or_cropped_columns": [],
        "has_blurry_pages": False,
        "has_skewed_pages": False,
        "pages": [{"contrast_score": 0.18, "blur_score": 120.0}],
    }
    document = _document(
        original_filename="DOC-010_internal_transfer_blurry_uncropped_photo.webp",
        stored_file_path=str(source_path),
        mime_type="image/webp",
        document_type=DocumentType.general_document,
    )

    metadata = processor._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert worker.calls[0][1] == "DOC-010_internal_transfer_blurry_uncropped_photo.webp"
    assert worker.calls[0][0] == source_path
    assert metadata is not None
    assert metadata["vl_preprocess_mode"] == "original"
    assert metadata["vl_preprocess_input"]["variant_name"] == "original"
    assert metadata["vl_preprocess_policy"]["selected_mode"] == "original"
    assert metadata["vl_preprocess_policy"]["reason"] == "scan_original_safe_default"
    assert metadata["vl_preprocess_policy"]["upscale_factor"] is None
    assert metadata["vl_preprocess_policy"]["contrast_mode"] is None
    assert metadata["vl_preprocess_policy"]["light_page_preprocess"]["used"] is False
    assert metadata["vl_preprocess_policy"]["light_page_preprocess"]["skip_reason"] == "debug_candidate_not_used_by_default"
    assert metadata["vl_preprocess_policy"]["current_standard"]["used"] is False
    assert metadata["vl_preprocess_policy"]["current_standard"]["skip_reason"] == "legacy_debug_only_not_used_by_default"
    assert "vl_input_candidate_comparison" not in metadata
    assert metadata["vl_provider_metadata"]["input_variant"]["variant_name"] == "original"
    assert "input_candidate_comparison" not in metadata["vl_provider_metadata"]
    assert metadata["vl_candidate_summary"]["parsed_line_item_count"] == 3
    assert "vl_candidate_preprocessed_retry_requires_review" not in metadata["vl_candidate_summary"]["issue_codes"]


def test_vl_upload_pipeline_uses_contrast_only_for_low_contrast_photo(tmp_path):
    text = """
    납품서
    문서번호 DN-2026-0003
    No 품목명 규격 수량 단위 비고
    1 S45C PIN 8X60 500 EA 입고대기
    2 SUS 볼트 M5X20 1000 EA 정상
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "pass",
            "text": text,
            "validation": {"status": "pass", "ok": True},
        }
    )
    processor = _processor(worker)
    source_path = tmp_path / "delivery-photo.jpg"
    _write_test_image(source_path, color=(96, 93, 90))
    contrast_path = tmp_path / "delivery-photo-vl-contrast-only.png"
    contrast_path.write_bytes(b"processed")
    processor._safe_quality_for_vl_input = lambda _path: {
        "likely_scan_type": "photo",
        "overall_quality_score": 0.52,
        "possible_right_column_crop": False,
        "hidden_or_cropped_columns": [],
        "has_blurry_pages": True,
        "has_skewed_pages": False,
        "pages": [{"contrast_score": 0.08, "blur_score": 42.0}],
    }
    processor.image_preprocessor.prepare_contrast_only_vl_input = lambda image_path, output_dir: {
        "variant_name": "contrast_only",
        "original_path": str(image_path),
        "processed_path": str(contrast_path),
        "operations": ["full_page_preserved", "contrast_only_no_crop", "full_page_light_local_contrast"],
        "warnings": ["no_crop_applied_preserve_full_document", "no_sharpen_or_denoise_applied"],
    }
    document = _document(
        original_filename="delivery-photo.jpg",
        stored_file_path=str(source_path),
        mime_type="image/jpeg",
        document_type=DocumentType.general_document,
    )

    metadata = processor._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert worker.calls == [(contrast_path, "delivery-photo.jpg")]
    assert metadata is not None
    assert metadata["vl_preprocess_mode"] == "contrast_only"
    assert metadata["vl_preprocess_input"]["variant_name"] == "contrast_only"
    assert metadata["vl_preprocess_policy"]["selected_mode"] == "contrast_only"
    assert metadata["vl_preprocess_policy"]["reason"] == "photo_low_contrast_contrast_only_no_crop"
    assert metadata["vl_preprocess_policy"]["page_crop_applied"] is False


def test_vl_upload_pipeline_keeps_original_when_hidden_column_risk(tmp_path):
    worker = FakeVLWorker({"ok": True, "provider": "paddleocr_vl_1_6_gguf", "text": "납품서"})
    processor = _processor(worker)
    source_path = tmp_path / "hidden-column.jpg"
    _write_test_image(source_path, color=(98, 95, 91))
    processor._safe_quality_for_vl_input = lambda _path: {
        "likely_scan_type": "photo",
        "overall_quality_score": 0.44,
        "possible_right_column_crop": True,
        "hidden_or_cropped_columns": ["tax_amount", "line_total"],
        "has_blurry_pages": True,
        "has_skewed_pages": False,
        "pages": [{"contrast_score": 0.07, "blur_score": 31.0}],
    }
    document = _document(
        original_filename="hidden-column.jpg",
        stored_file_path=str(source_path),
        mime_type="image/jpeg",
        document_type=DocumentType.general_document,
    )

    metadata = processor._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert worker.calls[0][1] == "hidden-column.jpg"
    assert worker.calls[0][0] == source_path
    assert metadata is not None
    assert metadata["vl_preprocess_mode"] == "original"
    assert metadata["vl_preprocess_policy"]["hidden_cropped_guardrail"] is True
    assert metadata["vl_preprocess_policy"]["reason"] == "hidden_or_cropped_column_risk_preserve_original_visible_frame"
    assert metadata["vl_preprocess_policy"]["page_crop_applied"] is False
    assert "hidden_or_cropped_column_risk_skip_preprocess" in metadata["vl_preprocess_policy"]["skipped_reasons"]


def test_final_business_safety_blocks_pos_summary_rows_from_manufacturing_items():
    document = _document(
        original_filename="DOC-004_pos_daily_settlement_blurry_screen_photo.jpg",
        document_type=DocumentType.general_document,
        title="일정산",
        document_number="POS-2026-0004",
        extracted_amount=Decimal("955900"),
        line_items=[
            {"item_name": "순판매금액", "line_total": 955900},
            {"item_name": "과세합계", "line_total": 869010},
            {"item_name": "주문횟수", "quantity": 22},
        ],
    )

    issues = DocumentProcessor()._apply_final_business_safety_overrides(
        document,
        "루팡 POS 메인포스 일정산 실 판매금액 955,900 주문횟수 22",
    )

    assert document.line_items == []
    assert document.document_type == DocumentType.general_document
    assert document.category == "pos_daily_settlement"
    assert "unsupported_pos_settlement" in document.tags
    assert document.review_required is True
    assert {issue["code"] for issue in issues} == {"unsupported_pos_daily_settlement_review_required"}


def test_final_business_safety_does_not_treat_transaction_statement_as_pos_from_settlement_word_only():
    document = _document(
        original_filename="DOC-010_transaction_statement_uncropped_photo.pdf",
        document_type=DocumentType.transaction_statement,
        vendor_name="상호: (주)태광부품",
        customer_name="고객사: 삼광유통",
        line_items=[
            {"item_name": "AL6061 판재", "quantity": 12, "unit_price": 18000, "line_total": 237600},
            {"item_name": "POS 영수증 용지", "quantity": 10, "unit_price": 33000, "line_total": 330000},
            {"item_name": "S45C 환봉", "quantity": 15, "unit_price": 12500, "line_total": 206250},
        ],
    )

    issues = DocumentProcessor()._apply_final_business_safety_overrides(
        document,
        "거래명세서 월말 정산 참고 POS 영수증 용지 결제합계 460,350 공급가액 418,500 세액 41,850 합계 460,350",
    )

    assert document.document_type == DocumentType.transaction_statement
    assert document.category != "unsupported_pos_settlement"
    assert len(document.line_items or []) == 3
    assert document.vendor_name == "태광부품"
    assert document.customer_name == "삼광유통"
    assert not any(issue["code"] == "unsupported_pos_daily_settlement_review_required" for issue in issues)


def test_final_business_safety_clears_person_label_from_party_name():
    document = _document(
        original_filename="DOC-066_purchase_order_uncropped_photo.webp",
        document_type=DocumentType.purchase_order,
        vendor_name="상호: (주)세진푸드",
        customer_name="담당: 김선영 / 회계팀",
        line_items=[{"item_name": "PCB Connector 12P", "quantity": 100}],
    )

    DocumentProcessor()._apply_final_business_safety_overrides(document, "발주서")

    assert document.vendor_name == "세진푸드"
    assert document.customer_name is None


def test_final_business_safety_removes_amounts_from_inspection_documents():
    document = _document(
        original_filename="DOC-001_incoming_inspection.pdf",
        document_type=DocumentType.invoice,
        extracted_amount=Decimal("81212"),
        subtotal=Decimal("80012"),
        tax=Decimal("1200"),
        currency="KRW",
        line_items=[
            {
                "item_name": "S45C PIN",
                "specification": "8X60",
                "quantity": 300,
                "unit_price": 1200,
                "supply_amount": 80012,
                "line_total": 81212,
            }
        ],
    )

    issues = DocumentProcessor()._apply_final_business_safety_overrides(
        document,
        "입고 검사 기록서 검사일 2026.06.15 품목 S45C PIN 입고수량 300 합격 300 불량 0 금액 항목 없음",
    )

    assert document.document_type == DocumentType.inspection_report
    assert document.extracted_amount is None
    assert document.subtotal is None
    assert document.tax is None
    assert document.currency is None
    assert document.line_items[0]["item_name"] == "S45C PIN"
    assert "unit_price" not in document.line_items[0]
    assert "supply_amount" not in document.line_items[0]
    assert "line_total" not in document.line_items[0]
    assert "no_price_document_amount_blocker" in {issue["code"] for issue in issues}


def test_final_business_safety_drops_summary_footer_rows_from_any_reader_path():
    document = _document(
        original_filename="DOC-009_return_credit_blurry_uncropped_photo.pdf",
        document_type=DocumentType.transaction_statement,
        line_items=[
            {"item_name": "S45C PIN", "quantity": 10, "line_total": 20000},
            {"item_name": "크레뒷합계", "line_total": 12100},
            {"item_name": "TOTAL USD / KRW Converted", "line_total": 1370},
            {"item_name": "옵션 선택 후 예상합계", "line_total": 500000},
        ],
    )

    issues = DocumentProcessor()._apply_final_business_safety_overrides(document, "반품 크레딧 메모")

    assert [item["item_name"] for item in document.line_items] == ["S45C PIN"]
    assert {issue["code"] for issue in issues} == {"summary_total_not_line_item"}


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


def test_vl_upload_pipeline_preserves_worker_inspection_tables_as_review_required_business_data():
    text = """
    입고 검사 기록서
    문서번호 IQC-REMOTE-007
    검사일 2026.06.15
    No 품목 규격 입고수량 합격 불량 판정 비고
    품목명 검사항목 판정 비고가 같은 줄에 섞여 보일 수 있음
    금액 항목 없음
    """
    worker = FakeVLWorker(
        {
            "ok": True,
            "provider": "paddleocr_vl_1_6_gguf",
            "classification": "pass",
            "text": text,
            "tables": [
                {
                    "table_type": "incoming_inspection",
                    "source": "vl_worker_table_extractor",
                    "schema_version": "docparse_vl_table_schema_v1",
                    "rows": [
                        {
                            "no": 1,
                            "item_name": "베어링 하우징",
                            "specification": "BH-220",
                            "received_quantity": 80,
                            "accepted_quantity": 78,
                            "defective_quantity": 2,
                            "result": "조건부합격",
                            "note": "표면 흠집",
                        },
                        {
                            "no": 2,
                            "item_name": "S45C PIN",
                            "specification": "8X60",
                            "received_quantity": 300,
                            "accepted_quantity": 300,
                            "defective_quantity": 0,
                            "result": "합격",
                        },
                    ],
                    "warnings": ["vl_table_review_required"],
                    "review_required": True,
                }
            ],
            "structured_schema": {"version": "docparse_vl_table_schema_v1"},
            "validation": {"status": "pass", "ok": True},
        }
    )
    document = _document(
        original_filename="incoming-inspection-worker-table.jpg",
        document_type=DocumentType.inspection_report,
        workflow_metadata={
            "taxonomy": {
                "document_profile": "quality_document",
                "document_profiles": ["quality_document", "no_price_document"],
            }
        },
    )

    metadata = _processor(worker)._vl_primary_reader_metadata(
        Path(document.stored_file_path),
        document,
        document.workflow_metadata,
    )

    assert metadata is not None
    assert metadata["vl_provider_metadata"]["structured_schema"]["version"] == "docparse_vl_table_schema_v1"
    assert metadata["vl_provider_metadata"]["table_count"] == 1
    assert metadata["vl_candidate_summary"]["gate_decision"] == "review_required"
    assert metadata["vl_candidate_summary"]["requires_review"] is True
    assert metadata["vl_candidate_summary"]["fallback_used"] is False
    assert metadata["vl_candidates"][0]["structured_candidate"]["tables"][0]["table_type"] == "incoming_inspection"
    assert document.document_type == DocumentType.inspection_report
    assert document.extracted_amount is None
    assert document.currency is None
    assert len(document.line_items or []) == 2
    assert document.line_items[0]["item_name"] == "베어링 하우징"
    assert document.line_items[0]["received_quantity"] == 80
    assert document.line_items[0]["accepted_quantity"] == 78
    assert document.line_items[0]["rejected_quantity"] == 2
    assert document.line_items[0]["defective_quantity"] == 2
    assert "unit_price" not in document.line_items[0]
    assert "line_total" not in document.line_items[0]
    issue_codes = metadata["vl_candidate_summary"]["issue_codes"]
    assert "vl_candidate_inspection_table_review_required" in issue_codes
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


def test_vl_upload_pipeline_suppresses_hidden_amount_columns_at_final_assignment_boundary():
    line_items = [
        {
            "item_name": "Linear Guide Rail HGW20",
            "quantity": 10,
            "unit": "EA",
            "unit_price": 45,
            "supply_amount": 450,
            "tax_amount": 0,
            "line_total": 450,
            "validation_warnings": ["row_amount_hidden_do_not_infer"],
        }
    ]

    safe_items = DocumentProcessor()._line_items_for_extraction_method(
        line_items,
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert safe_items[0]["unit_price"] == 45
    assert "supply_amount" not in safe_items[0]
    assert "tax_amount" not in safe_items[0]
    assert "line_total" not in safe_items[0]
    assert "vl_amount_suppressed_due_to_hidden_or_unverified_column" in safe_items[0]["review_flags"]


def test_vl_upload_pipeline_preserves_visible_signed_return_credit_amount_rows():
    line_items = [
        {
            "item_name": "AL6061 판재",
            "specification": "3T 400x600",
            "quantity": -2,
            "unit_price": 18000,
            "supply_amount": -36000,
            "tax_amount": -3600,
            "line_total": -39600,
            "validation_warnings": ["line_total_not_visible_do_not_infer"],
        },
        {
            "item_name": "반품 운송비",
            "quantity": 1,
            "unit_price": 5000,
            "supply_amount": 5000,
            "tax_amount": 500,
            "line_total": 5500,
        },
    ]

    safe_items = DocumentProcessor()._line_items_for_extraction_method(
        line_items,
        "paddleocr_vl_1_6_gguf_primary_reader",
        preserve_signed_amount_rows=True,
    )

    assert safe_items[0]["quantity"] == -2
    assert safe_items[0]["unit_price"] == 18000
    assert safe_items[0]["supply_amount"] == -36000
    assert safe_items[0]["tax_amount"] == -3600
    assert safe_items[0]["line_total"] == -39600
    assert "vl_amount_suppressed_due_to_hidden_or_unverified_column" not in safe_items[0].get("review_flags", [])
    assert safe_items[1]["supply_amount"] == 5000
    assert safe_items[1]["tax_amount"] == 500
    assert safe_items[1]["line_total"] == 5500


def test_vl_upload_pipeline_keeps_hidden_amount_guardrail_for_signed_rows():
    line_items = [
        {
            "item_name": "AL6061 판재",
            "quantity": -2,
            "unit_price": 18000,
            "supply_amount": -36000,
            "tax_amount": -3600,
            "line_total": -39600,
            "validation_warnings": ["row_amount_hidden_do_not_infer"],
        }
    ]

    safe_items = DocumentProcessor()._line_items_for_extraction_method(
        line_items,
        "paddleocr_vl_1_6_gguf_primary_reader",
        preserve_signed_amount_rows=True,
    )

    assert safe_items[0]["quantity"] == -2
    assert safe_items[0]["unit_price"] == 18000
    assert "supply_amount" not in safe_items[0]
    assert "tax_amount" not in safe_items[0]
    assert "line_total" not in safe_items[0]
    assert "vl_amount_suppressed_due_to_hidden_or_unverified_column" in safe_items[0]["review_flags"]


def test_vl_upload_pipeline_classifies_return_credit_category_from_visible_text():
    parsed = ParsedDocument(
        document_type=DocumentType.general_document,
        category="credit_note",
        tags=["return_document"],
    )
    raw_text = "\n".join([
        "반품/크레딧 메모",
        "문서번호 RCM-2026-0009",
        "사유 규격 불일치",
    ])

    processor = DocumentProcessor()

    assert processor._is_return_or_credit_parsed_document(parsed, raw_text)
    assert processor._return_or_credit_category(parsed, raw_text) == "credit_note"


def test_vl_upload_pipeline_restores_return_credit_visible_amounts_after_matching():
    final_items = [
        {
            "item_name": "AL6061 판재 3T",
            "source_item_name": "AL6061 판재 3T",
            "specification": "400x600",
            "quantity": -2,
            "unit_price": 18000,
            "validation_warnings": ["unit_not_visible", "vl_amount_suppressed_due_to_hidden_or_unverified_column"],
            "review_flags": ["vl_amount_suppressed_due_to_hidden_or_unverified_column"],
            "item_master_match_status": "unmatched",
        },
        {
            "item_name": "반품 운송비",
            "source_item_name": "반품 운송비",
            "quantity": 1,
            "unit_price": 5000,
            "validation_warnings": ["unit_not_visible", "vl_amount_suppressed_due_to_hidden_or_unverified_column"],
            "review_flags": ["vl_amount_suppressed_due_to_hidden_or_unverified_column"],
            "item_master_match_status": "unmatched",
        },
    ]
    parsed_items = [
        {
            "item_name": "AL6061 판재",
            "specification": "3T 400x600",
            "quantity": -2,
            "unit_price": 18000,
            "supply_amount": -36000,
            "tax_amount": -3600,
            "line_total": -39600,
        },
        {
            "item_name": "반품 운송비",
            "quantity": 1,
            "unit_price": 5000,
            "supply_amount": 5000,
            "tax_amount": 500,
            "line_total": 5500,
        },
    ]

    restored = DocumentProcessor()._restore_return_credit_visible_amounts(final_items, parsed_items)

    assert restored[0]["supply_amount"] == -36000
    assert restored[0]["tax_amount"] == -3600
    assert restored[0]["line_total"] == -39600
    assert restored[1]["supply_amount"] == 5000
    assert restored[1]["tax_amount"] == 500
    assert restored[1]["line_total"] == 5500
    assert "vl_amount_suppressed_due_to_hidden_or_unverified_column" not in restored[0].get("validation_warnings", [])
    assert "vl_amount_suppressed_due_to_hidden_or_unverified_column" not in restored[1].get("review_flags", [])


def test_vl_upload_pipeline_preserves_signed_amounts_when_structured_candidate_updates_parsed_return_credit():
    parsed = ParsedDocument(
        document_type=DocumentType.general_document,
        document_number="RCM-2026-0009",
        category="credit_note",
        tags=["return_document"],
        line_items=[],
    )
    structured = {
        "document": {
            "document_type": "general_document",
            "document_subtype": "credit_note",
            "document_number": "RCM-2026-0009",
        },
        "line_items": [
            {
                "item_name": "AL6061 판재",
                "specification": "3T 400x600",
                "quantity": -2,
                "unit_price": 18000,
                "supply_amount": -36000,
                "tax_amount": -3600,
                "line_total": -39600,
                "validation_warnings": ["line_total_not_visible_do_not_infer"],
            }
        ],
    }

    DocumentProcessor()._apply_vl_structured_candidate_to_parsed(parsed, structured)

    assert parsed.line_items[0]["quantity"] == -2
    assert parsed.line_items[0]["supply_amount"] == -36000
    assert parsed.line_items[0]["tax_amount"] == -3600
    assert parsed.line_items[0]["line_total"] == -39600


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
    ai_parsed = result.workflow_metadata["ai_parsed_document"]
    assert ai_parsed["document_type_hint"] in {"internal_transfer", "inventory_movement_document"}
    assert any(section["type"] == "key_value" for section in ai_parsed["sections"])
    assert any(section["type"] == "table" for section in ai_parsed["sections"])
    assert ai_parsed["policy"]["amount_allowed"] is False
    assert len(result.line_items or []) == 2
    assert result.line_items[0]["quantity"] == 2
    assert result.line_items[0]["requested_quantity"] == 2
    assert "supply_amount" not in result.line_items[0]


def test_final_business_safety_prevents_return_credit_purchase_order_and_preserves_negative_values():
    document = _document(
        original_filename="DOC-009_return_credit_blurry_uncropped_photo.pdf",
        document_type=DocumentType.purchase_order,
        document_number="RCM-2026-0009",
        line_items=[
            {
                "item_name": "AL6061 판재",
                "quantity": -2,
                "unit_price": 18000,
                "supply_amount": -36000,
                "tax_amount": -3600,
                "line_total": -39600,
            }
        ],
    )

    issues = DocumentProcessor()._apply_final_business_safety_overrides(
        document,
        "반품/크레딧 메모 문서번호 RCM-2026-0009 원문서 TS-2026-0034 사유 규격 불일치",
    )

    assert document.document_type == DocumentType.general_document
    assert document.category == "credit_note"
    assert "return_document" in document.tags
    assert document.line_items[0]["quantity"] == -2
    assert document.line_items[0]["supply_amount"] == -36000
    assert document.line_items[0]["tax_amount"] == -3600
    assert document.line_items[0]["line_total"] == -39600
    assert "return_credit_not_purchase_order" in {issue["code"] for issue in issues}


def test_ai_parsed_document_key_values_fill_missing_canonical_fields_without_overwriting():
    processor = DocumentProcessor()
    document = _document(
        document_type=DocumentType.general_document,
        document_number=None,
        vendor_name=None,
        customer_name=None,
        issue_date=None,
        extracted_date=None,
    )
    workflow_metadata = {}
    ai_doc = {
        "sections": [
            {
                "type": "key_value",
                "fields": [
                    {"key": "문서번호", "value": "MV-2026-0010", "normalized_key": "document_number"},
                    {"key": "요청일", "value": "2026.06.18", "normalized_key": "document_date"},
                    {"key": "공급자", "value": "대성정공", "normalized_key": "supplier_name"},
                    {"key": "거래처", "value": "한빛정밀", "normalized_key": "customer_name"},
                ],
            }
        ],
        "policy": {"amount_allowed": False},
    }

    issues = processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "자재 이동 요청서 문서번호 MV-2026-0010 요청일 2026.06.18",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert issues == []
    assert document.document_number == "MV-2026-0010"
    assert document.issue_date == date(2026, 6, 18)
    assert document.vendor_name is None
    assert document.customer_name is None
    assert document.field_sources["document_number"] == "ai_parsed_document.key_value"
    assert workflow_metadata["ai_parsed_document_mapping"]["applied_fields"] == [
        "document_number",
        "issue_date",
    ]
    review_only = workflow_metadata["ai_parsed_document_mapping"]["review_only_fields"]
    assert {field["value"] for field in review_only if field["reason"] == "party_candidate_review_required"} == {
        "대성정공",
        "한빛정밀",
    }


def test_ai_parsed_document_table_rows_promote_safe_no_price_line_item_candidates():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.delivery_note, line_items=[])
    workflow_metadata = {}
    ai_doc = {
        "policy": {"amount_allowed": False},
        "sections": [
            {
                "type": "table",
                "title": "납품 목록",
                "columns": ["No", "품목", "규격", "수량", "단위", "비고"],
                "rows": [
                    {
                        "row_index": 1,
                        "cells": {"품목": "S45C PIN", "규격": "8X60", "수량": "500", "단위": "EA", "비고": "입고대기"},
                        "canonical_cells": {
                            "item_name": "S45C PIN",
                            "specification": "8X60",
                            "quantity": "500",
                            "unit": "EA",
                            "note": "입고대기",
                            "line_total": "999999",
                        },
                    }
                ],
            }
        ],
    }

    issues = processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "납품서 단가 미기재 납품서 - 수량 검수용",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.line_items[0]["item_name"] == "S45C PIN"
    assert document.line_items[0]["quantity"] == "500"
    assert "line_total" not in document.line_items[0]
    assert "ai_parsed_document_table_candidate_promoted_for_review" in {issue["code"] for issue in issues}
    assert workflow_metadata["ai_parsed_document_mapping"]["table_line_items_added"] == 1


def test_ai_parsed_document_table_row_does_not_duplicate_existing_code_row():
    processor = DocumentProcessor()
    document = _document(
        document_type=DocumentType.transaction_statement,
        line_items=[
            {
                "item_name": "스테인리스 브라켓",
                "document_item_code": "BRK-SUS",
                "quantity": 5,
                "unit": "EA",
                "unit_price": 4300,
                "line_total": 21500,
            }
        ],
    )
    workflow_metadata = {}
    ai_doc = {
        "policy": {"amount_allowed": True},
        "sections": [
            {
                "type": "table",
                "rows": [
                    {
                        "row_index": 1,
                        "canonical_cells": {
                            "item_name": "스테인리스 브라젯",
                            "specification": "BRK-SUS",
                            "quantity": "5",
                            "unit": "EA",
                            "unit_price": "4,300",
                            "line_total": "21,500",
                        },
                    }
                ],
            }
        ],
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "거래명세서 스테인리스 브라켓 BRK-SUS 5 EA 4,300 21,500",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert len(document.line_items or []) == 1
    mapping = workflow_metadata["ai_parsed_document_mapping"]
    assert mapping["table_line_items_added"] == 0
    assert mapping["table_line_items_skipped_reasons"] == ["duplicate_existing_line_item"]


def test_final_business_safety_removes_confirmed_duplicate_code_line_item():
    processor = DocumentProcessor()
    document = _document(
        document_type=DocumentType.transaction_statement,
        line_items=[
            {
                "item_name": "스테인리스 브라켓",
                "document_item_code": "BRK-SUS",
                "quantity": 5,
                "unit": "EA",
                "unit_price": 4300,
                "line_total": 21500,
            },
            {
                "item_name": "스테인리스 브라젯",
                "specification": "BRK-SUS",
                "quantity": "5",
                "unit": "EA",
                "unit_price": "4,300",
                "line_total": "21,500",
            },
        ],
    )

    issues = processor._apply_final_business_safety_overrides(
        document,
        "거래명세서 스테인리스 브라켓 BRK-SUS 5 EA 4,300 21,500",
    )

    assert len(document.line_items or []) == 1
    assert document.line_items[0]["document_item_code"] == "BRK-SUS"
    assert "duplicate_line_item_removed" in {issue["code"] for issue in issues}


def test_ai_parsed_document_table_repairs_invalid_vl_amount_shifted_row():
    processor = DocumentProcessor()
    document = _document(
        document_type=DocumentType.invoice,
        category="tax_invoice",
        tags=["tax_invoice"],
        line_items=[
            {
                "item_name": "06.08 S45C PIN",
                "document_item_code": "PIN-8X60",
                "quantity": 7,
                "unit_price": 50,
                "tax_amount": 1750,
                "line_total": 350,
                "validation_warnings": ["invalid_tax_greater_than_total"],
            }
        ],
    )
    workflow_metadata = {}
    ai_doc = {
        "policy": {"amount_allowed": True},
        "sections": [
            {
                "type": "table",
                "rows": [
                    {
                        "row_index": 1,
                        "canonical_cells": {
                            "item_name": "S45C PIN 8X60",
                            "specification": "PIN-8X60",
                            "quantity": "50",
                            "unit_price": "350",
                            "supply_amount": "17,500",
                            "tax_amount": "1,750",
                        },
                    }
                ],
            }
        ],
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "세금계산서 06.08 S45C PIN 8X60 수량 50 단가 350 공급가액 17,500 세액 1,750",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert len(document.line_items or []) == 1
    repaired = document.line_items[0]
    assert repaired["item_name"] == "S45C PIN 8X60"
    assert repaired["quantity"] == "50"
    assert repaired["unit_price"] == "350"
    assert "ai_parsed_document_table_repaired_invalid_vl_row" in repaired["review_flags"]
    assert workflow_metadata["ai_parsed_document_mapping"]["table_line_items_repaired"] == 1


def test_ai_parsed_document_pos_settlement_table_is_not_promoted_to_line_items():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.general_document, line_items=[])
    workflow_metadata = {}
    ai_doc = {
        "policy": {"amount_allowed": False},
        "sections": [
            {
                "type": "table",
                "table_type_guess": "settlement_summary",
                "columns": ["항목", "금액"],
                "rows": [
                    {
                        "row_index": 1,
                        "cells": {"항목": "순판매금액", "금액": "955,900"},
                        "canonical_cells": {"item_name": "순판매금액", "line_total": "955900"},
                    }
                ],
            }
        ],
    }

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "루팡 POS 메인포스 일정산 순판매금액 955,900 카드합계 891,600",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.line_items == []
    assert workflow_metadata["ai_parsed_document_mapping"]["table_line_items_skipped_reasons"] == [
        "no_safe_promotable_table_rows"
    ]


def test_ai_parsed_document_inspection_table_with_pos_item_is_preserved_not_pos_settlement():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.general_document, category="pos_daily_settlement", tags=["incoming_inspection"], line_items=[])
    workflow_metadata = {}
    ai_doc = {
        "policy": {"amount_allowed": False},
        "sections": [
            {
                "type": "table",
                "title": "입고 검사 목록",
                "columns": ["No", "품명", "Lot Code", "입고수량", "판정", "비고"],
                "rows": [
                    {
                        "row_index": 1,
                        "cells": {"품명": "POS 영수증 용지", "Lot Code": "POS-PAPER", "입고수량": "50", "판정": "재검", "비고": "스크래치 확인"},
                        "canonical_cells": {
                            "item_name": "POS 영수증 용지",
                            "lot_code": "POS-PAPER",
                            "received_quantity": "50",
                            "inspection_result": "재검",
                            "note": "스크래치 확인",
                            "line_total": "999999",
                        },
                    }
                ],
            }
        ],
    }

    issues = processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "입고 검사기록서 검사항목 판정 POS 영수증 용지 POS-PAPER 50 재검",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert document.category != "pos_daily_settlement"
    assert document.line_items[0]["item_name"] == "POS 영수증 용지"
    assert "line_total" not in document.line_items[0]
    assert workflow_metadata["inspection_row_candidates"][0]["fields"]["item_name"] == "POS 영수증 용지"
    assert "ai_parsed_document_table_candidate_promoted_for_review" in {issue["code"] for issue in issues}


def test_ai_parsed_document_receipt_fragments_are_review_only_candidates():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.receipt, tags=["receipt"], line_items=[])
    workflow_metadata = {}
    ai_doc = {"sections": [{"type": "notes", "items": ["영수증"]}], "policy": {"amount_allowed": True}}
    raw_text = "청년식당\nPCB Connector 12P\n5EA X 620 3100\n합계 3,100\n카드승인번호 RC-001"

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        raw_text,
        "image_ocr_fast_path",
    )

    assert document.line_items == []
    candidate = workflow_metadata["receipt_item_candidates"][0]
    assert candidate["status"] == "review_only"
    assert candidate["item_name"] == "PCB Connector 12P"
    assert candidate["quantity"] == "5"
    assert candidate["line_total"] == "3100"


def test_tax_invoice_approval_number_date_does_not_override_visible_row_date():
    processor = DocumentProcessor()
    document = _document(
        document_type=DocumentType.invoice,
        category="tax_invoice",
        tags=["tax_invoice"],
        issue_date=date(2026, 6, 16),
        extracted_date=date(2026, 6, 16),
    )

    issues = processor._apply_final_business_safety_overrides(
        document,
        "\n".join(
            [
                "세금계산서",
                "전자세금계산서 승인번호: 20260616-TEST",
                "06.11 M3 육각너트 NUT-M3 12 18 216 22",
                "06.11 HDPE 포장필름 FILM-HDPE 12 56,000 672,000 67,200",
            ]
        ),
    )

    assert document.issue_date == date(2026, 6, 11)
    assert document.extracted_date == date(2026, 6, 11)
    assert document.field_sources["issue_date"] == "tax_invoice_visible_row_date_guard"
    assert "tax_invoice_approval_date_not_issue_date" in {issue["code"] for issue in issues}


def test_receipt_with_pos_item_name_is_not_pos_daily_settlement():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.receipt, tags=["receipt"], line_items=[])

    assert not processor._looks_like_pos_settlement_document(
        document,
        "대성식자재 영수증번호 DOC-026 POS 영수증 용지 5BOX 33000 165000 합계 531320",
        [],
    )


def test_inspection_context_does_not_create_receipt_candidates_from_pos_item_name():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.inspection_report, tags=["inspection_report"], line_items=[])
    ai_doc = {
        "sections": [
            {
                "type": "table",
                "title": "입고 검사 목록",
                "columns": ["품명", "입고수량", "판정"],
                "rows": [],
            }
        ]
    }

    result = processor._build_ai_review_row_candidates(
        document,
        ai_doc,
        "입고 검사기록서 POS 영수증 용지 POS-PAPER 50 외관/치수 재검",
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    assert result["receipt_item_candidates"] == []


def test_ai_parsed_document_purchase_memo_rows_are_requested_item_candidates():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.memo, category="purchase_memo", tags=["purchase_memo"], line_items=[])
    workflow_metadata = {}
    ai_doc = {"sections": [{"type": "notes", "items": ["구매 메모"]}], "policy": {"amount_allowed": False}}
    raw_text = "구매 메모\n-SUS 볼트 M5x20 300EA 단가 확인필요\n- PCB Connector 12P 50EA 단가 620"

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        raw_text,
        "paddleocr_vl_1_6_gguf_primary_reader",
    )

    candidates = workflow_metadata["requested_item_candidates"]
    assert [candidate["item_name"] for candidate in candidates] == ["SUS 볼트 M5x20", "PCB Connector 12P"]
    assert candidates[0]["price_status"] == "review_required"
    assert candidates[1]["unit_price"] == "620"
    assert document.line_items == []


def test_ai_parsed_document_records_gap_analysis_when_rows_are_missing_after_fast_path():
    processor = DocumentProcessor()
    document = _document(document_type=DocumentType.purchase_order, line_items=[])
    workflow_metadata = {}
    ai_doc = {"sections": [{"type": "notes", "items": ["Puchne Orde"]}], "policy": {"amount_allowed": True}}

    processor._apply_ai_parsed_document_candidates(
        document,
        ai_doc,
        workflow_metadata,
        "Puchne Orde 총목코드 공급가역",
        "image_ocr_fast_path",
    )

    analysis = workflow_metadata["line_item_gap_analysis"]
    assert analysis["reason"] == "raw_text_missing_rows+vl_fallback_fast_path"
    assert analysis["status"] == "no_review_row_candidates"


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


def test_process_uses_official_table_without_text_and_skips_ppocr_ingestion(tmp_path):
    path = tmp_path / "incoming-inspection.png"
    path.write_bytes(b"fake image")
    document = Document(
        original_filename="incoming-inspection.png",
        stored_file_path=str(path),
        mime_type="image/png",
        processing_status=ProcessingStatus.uploaded,
        workflow_metadata={
            "taxonomy": {
                "document_profile": "quality_document",
                "document_profiles": ["quality_document", "no_price_document"],
            }
        },
    )
    official_table = {
        "table_type": "incoming_inspection",
        "source": "paddleocrvl_official_table_html",
        "columns": ["No", "품명", "Lot/Code", "입고수량", "검사항목", "판정", "비고"],
        "rows": [
            {
                "no": 1,
                "item_name": "스테인리스 브라젯",
                "document_item_code": "BRK-SUS",
                "received_quantity": 20,
                "inspection_item": "외관/치수",
                "result": "합격",
                "note": "이상 없음",
                "review_flags": ["paddleocrvl_official_table_review_required"],
            },
            {
                "no": 2,
                "item_name": "SUS 볼트",
                "specification": "M5x20",
                "document_item_code": "BOLT-M5X20",
                "received_quantity": 120,
                "inspection_item": "외관/치수",
                "result": "합격",
                "note": "치수 재확인",
                "review_flags": ["paddleocrvl_official_table_review_required"],
            },
            {
                "no": 3,
                "item_name": "PCB Connector 12P",
                "document_item_code": "CONN-12P",
                "received_quantity": 20,
                "inspection_item": "외관/치수",
                "result": "합격",
                "note": "이상 없음",
                "review_flags": ["paddleocrvl_official_table_review_required"],
            },
        ],
        "warnings": ["paddleocrvl_official_table_review_required", "inspection_report_no_amount_fields"],
        "review_required": True,
    }
    processor = _processor(
        FakeVLWorker(
            {
                "ok": True,
                "provider": "paddleocr_vl_1_6_gguf",
                "classification": "warn",
                "text": "",
                "tables": [official_table],
                "validation": {"status": "warn", "ok": False},
            }
        )
    )

    class BrokenIngestion:
        def ingest(self, *args, **kwargs):
            raise AssertionError("PP-OCRv4 ingestion should be skipped for official table output")

    processor.ingestion = BrokenIngestion()
    processor._document_quality_for_source = lambda *args, **kwargs: (None, [])

    result = processor.process(FakeSession(document), document)

    assert result.extraction_method == "paddleocr_vl_1_6_gguf_primary_reader"
    assert result.processing_status == ProcessingStatus.needs_review
    assert result.document_type == DocumentType.inspection_report
    assert len(result.line_items or []) == 3
    assert result.line_items[0]["item_name"] == "스테인리스 브라젯"
    assert result.line_items[0]["received_quantity"] == 20
    assert result.line_items[1]["specification"] == "M5x20"
    assert all("supply_amount" not in item and "line_total" not in item for item in result.line_items or [])
    summary = result.workflow_metadata["vl_candidate_summary"]
    assert summary["provider_available_candidate"] is True
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
