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
    source_path.write_bytes(b"fake image bytes")
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

    assert worker.calls == [(source_path, "DOC-010_internal_transfer_blurry_uncropped_photo.webp")]
    assert metadata is not None
    assert metadata["vl_preprocess_mode"] == "original"
    assert metadata["vl_preprocess_input"]["variant_name"] == "original"
    assert metadata["vl_preprocess_policy"]["selected_mode"] == "original"
    assert metadata["vl_preprocess_policy"]["current_standard"]["used"] is False
    assert metadata["vl_preprocess_policy"]["current_standard"]["skip_reason"] == "legacy_debug_only_not_used_by_default"
    assert "vl_input_candidate_comparison" not in metadata
    assert metadata["vl_provider_metadata"]["input_variant"]["variant_name"] == "original"
    assert "input_candidate_comparison" not in metadata["vl_provider_metadata"]
    assert metadata["vl_candidate_summary"]["parsed_line_item_count"] == 3
    assert "vl_candidate_preprocessed_retry_requires_review" not in metadata["vl_candidate_summary"]["issue_codes"]


def test_vl_upload_pipeline_can_use_contrast_only_for_low_contrast_photo(tmp_path):
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
    source_path.write_bytes(b"fake image bytes")
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
        "operations": ["full_page_light_background_normalization", "full_page_light_local_contrast"],
        "warnings": ["no_crop_applied_preserve_full_document"],
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
    assert metadata["vl_preprocess_policy"]["reason"] == "photo_or_low_contrast_light_contrast_only"


def test_vl_upload_pipeline_keeps_original_when_hidden_column_risk(tmp_path):
    worker = FakeVLWorker({"ok": True, "provider": "paddleocr_vl_1_6_gguf", "text": "납품서"})
    processor = _processor(worker)
    source_path = tmp_path / "hidden-column.jpg"
    source_path.write_bytes(b"fake image bytes")
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

    assert worker.calls == [(source_path, "hidden-column.jpg")]
    assert metadata is not None
    assert metadata["vl_preprocess_mode"] == "original"
    assert metadata["vl_preprocess_policy"]["hidden_cropped_guardrail"] is True
    assert metadata["vl_preprocess_policy"]["reason"] == "hidden_or_cropped_column_risk_original_required"


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
    assert document.category == "unsupported_pos_settlement"
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
