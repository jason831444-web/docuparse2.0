from decimal import Decimal
import sys
from types import SimpleNamespace

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.models.document import Document, DocumentType
from app.services.document_processor import DocumentProcessor
from app.services.file_ingestion import NormalizedDocument
from app.services.raw_extraction_snapshot import RawExtractionSnapshotService
from app.services.semantic_mapping import SemanticMappingService


def _candidate(text: str, x_min: float, y_min: float, x_max: float, y_max: float, confidence: float = 0.95):
    return {
        "text": text,
        "confidence": confidence,
        "page": 1,
        "bbox": [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]],
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
    }


def test_bbox_layout_candidates_are_metadata_only_and_do_not_promote_line_items():
    processor = DocumentProcessor()
    document = Document(
        original_filename="fax.pdf",
        stored_file_path="/tmp/fax.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.purchase_order,
        document_number="FAX-PO-2026-0921",
        extracted_amount=Decimal("418000"),
        currency="KRW",
        line_items=[
            {"item_name": "베어링하우징", "line_total": 176000},
            {"item_name": "S45C PIN 8X60", "line_total": 66000},
        ],
    )
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text="",
        raw_extracted_blocks=[{
            "type": "pdf_page_ocr",
            "page": 1,
            "line_candidates": [
                _candidate("품목명", 270, 400, 340, 420),
                _candidate("공급가액", 770, 400, 840, 420),
                _candidate("베어링하우징", 270, 430, 360, 450),
                _candidate("16000", 770, 430, 820, 450),
                _candidate("176000", 950, 430, 1010, 450),
                _candidate("S45C PIN 8X6Q", 270, 460, 390, 480),
                _candidate("6000", 770, 460, 820, 480),
                _candidate("66000", 950, 460, 1010, 480),
                _candidate("16000", 770, 490, 820, 510),
                _candidate("1600C", 870, 490, 920, 510),
                _candidate("176000", 950, 490, 1010, 510),
            ],
        }],
    )

    metadata = processor._bbox_layout_debug_metadata(normalized, document, {"document_profile": "priced_document"})

    assert len(document.line_items) == 2
    assert metadata is not None
    assert metadata["parser_integrated"] is False
    assert metadata["reconstructed_candidate_count"] == 3
    assert metadata["candidate_count"] == 1
    assert metadata["confirmed_line_item_count"] == 2
    assert metadata["uncertain_count"] == 1
    assert metadata["bbox_table_candidates"][0]["item_name"] is None
    assert "missing_item_name_from_ocr" in metadata["bbox_table_candidates"][0]["review_flags"]


def test_bbox_layout_metadata_does_not_create_amount_candidates_for_no_price_documents():
    processor = DocumentProcessor()
    document = Document(
        original_filename="transfer.pdf",
        stored_file_path="/tmp/transfer.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="TRF-2026-0922-002",
        currency=None,
        extracted_amount=None,
        line_items=[{"item_name": "내부 이동품", "quantity": 25, "unit": "EA"}],
    )
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text="",
        raw_extracted_blocks=[{
            "type": "pdf_page_ocr",
            "page": 1,
            "line_candidates": [
                _candidate("품목명", 100, 100, 160, 120),
                _candidate("요청수량", 300, 100, 380, 120),
                _candidate("내부 이동품", 100, 150, 190, 170),
                _candidate("25", 300, 150, 330, 170),
                _candidate("창고 이동", 100, 200, 170, 220),
                _candidate("비고", 420, 100, 460, 120),
            ],
        }],
    )

    metadata = processor._bbox_layout_debug_metadata(
        normalized,
        document,
        {"document_profile": "inventory_movement_document", "document_profiles": ["inventory_movement_document", "no_price_document"]},
    )

    assert metadata is not None
    assert metadata["parser_integrated"] is False
    assert metadata["bbox_table_candidates"] == []
    assert metadata["bbox_review_flags"] == []


def test_bbox_layout_metadata_filters_candidates_that_duplicate_confirmed_items():
    processor = DocumentProcessor()
    document = Document(
        original_filename="return.pdf",
        stored_file_path="/tmp/return.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="RTN-2026-0919-011",
        extracted_amount=Decimal("12100"),
        currency="KRW",
        line_items=[
            {"item_name": "베어링하우징", "line_total": 8000},
        ],
    )
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text="",
        raw_extracted_blocks=[{
            "type": "pdf_page_ocr",
            "page": 1,
            "line_candidates": [
                _candidate("품목명", 270, 400, 340, 420),
                _candidate("공급가액", 770, 400, 840, 420),
                _candidate("베어링하우징", 270, 430, 360, 450),
                _candidate("8000", 770, 430, 820, 450),
                _candidate("8800", 950, 430, 1010, 450),
                _candidate("베어링하우징 100mm B80C", 270, 460, 470, 480),
                _candidate("8000", 770, 460, 820, 480),
                _candidate("880C", 950, 460, 1010, 480),
            ],
        }],
    )

    metadata = processor._bbox_layout_debug_metadata(normalized, document, {"document_profile": "priced_document"})

    assert metadata is not None
    assert metadata["parser_integrated"] is False
    assert metadata["reconstructed_candidate_count"] >= 2
    assert metadata["candidate_count"] == 0
    assert metadata["bbox_table_candidates"] == []


def test_bbox_layout_metadata_filters_ocr_variant_duplicate_confirmed_items():
    processor = DocumentProcessor()
    document = Document(
        original_filename="return.pdf",
        stored_file_path="/tmp/return.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="RTN-2026-0919-011",
        extracted_amount=Decimal("12100"),
        currency="KRW",
        line_items=[
            {"item_name": "S45C PIN 8X6 C3000", "line_total": 12100},
        ],
    )
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text="",
        raw_extracted_blocks=[{
            "type": "pdf_page_ocr",
            "page": 1,
            "line_candidates": [
                _candidate("품목명", 270, 400, 340, 420),
                _candidate("공급가액", 770, 400, 840, 420),
                _candidate("S45C PIN 8X6 C3000", 270, 430, 430, 450),
                _candidate("3000", 770, 430, 820, 450),
                _candidate("3300", 950, 430, 1010, 450),
                _candidate("S45C PIN 8X6C 8X60", 270, 460, 470, 480),
                _candidate("3000", 770, 460, 820, 480),
                _candidate("330C", 950, 460, 1010, 480),
            ],
        }],
    )

    metadata = processor._bbox_layout_debug_metadata(normalized, document, {"document_profile": "priced_document"})

    assert metadata is not None
    assert metadata["reconstructed_candidate_count"] >= 2
    assert metadata["candidate_count"] == 0
    assert metadata["bbox_table_candidates"] == []


def test_raw_extraction_key_values_ignore_direct_vl_and_ocr_candidates():
    document = Document(
        original_filename="transfer.pdf",
        stored_file_path="/tmp/transfer.pdf",
        mime_type="application/pdf",
        extraction_method="paddleocr_vl",
        raw_text="문서번호: DOC-RAW",
        workflow_metadata={
            "vl_candidates": [
                {
                    "key_values": [
                        {
                            "key": "문서번호",
                            "value": "DOC-007",
                            "bbox": [0.10, 0.10, 0.30, 0.13],
                            "key_bbox": [0.10, 0.10, 0.18, 0.13],
                            "value_bbox": [0.19, 0.10, 0.30, 0.13],
                            "page_index": 0,
                            "confidence": 0.88,
                        }
                    ],
                    "structured_candidate": {"key_values": []},
                }
            ]
        },
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="processing_pipeline",
        line_candidates=[
            {
                "text": "문서번호: DOC-007",
                "x_min": 10,
                "y_min": 10,
                "x_max": 30,
                "y_max": 13,
                "confidence": 0.95,
            }
        ],
    )

    matches = [
        item
        for item in snapshot["key_values"]
        if item.get("key") == "문서번호"
    ]
    assert len(matches) == 1
    assert matches[0]["value"] == "DOC-RAW"
    assert matches[0]["source"] == "vl_raw_text_key_value"
    assert "normalized_bbox" not in matches[0]
    assert "key_bbox" not in matches[0]
    assert "value_bbox" not in matches[0]
    assert "bbox_source" not in matches[0]


def test_raw_extraction_keeps_plain_key_values_without_bbox_fields():
    document = Document(
        original_filename="quote.pdf",
        stored_file_path="/tmp/quote.pdf",
        mime_type="application/pdf",
        raw_text="유효기간: 견적일로부터 14일",
        workflow_metadata={
            "vl_candidates": [
                {
                    "key_values": [
                        {"key": "문서번호", "value": "DOC-003", "source": "vl_text_block_key_value_bbox", "bbox": [0.1, 0.1, 0.2, 0.12]},
                        {"key": "작성일", "value": "2026.06.07", "source": "vl_text_block_key_value_bbox", "bbox": [0.5, 0.2, 0.7, 0.22]},
                        {"key": "공급자 상호", "value": "(주)미래테크", "source": "vl_block_postprocess_bbox", "bbox": [0.1, 0.2, 0.3, 0.22]},
                        {"key": "공급받는자 상호", "value": "(주)시흥대야점", "source": "vl_block_postprocess_bbox", "bbox": [0.5, 0.25, 0.7, 0.27]},
                    ],
                    "structured_candidate": {"key_values": []},
                }
            ]
        },
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="processing_pipeline",
        line_candidates=[
            {"text": "유효기간: 견적일로부터 14일", "x_min": 100, "y_min": 100, "x_max": 300, "y_max": 130}
        ],
    )

    assert "coverage_summary" not in snapshot
    assert all("normalized_bbox" not in item and "bbox_source" not in item for item in snapshot["key_values"])
    assert snapshot["key_values"] == [{"key": "유효기간", "value": "견적일로부터 14일", "source": "raw_text_key_value"}]


def test_raw_extraction_table_preserves_raw_columns_for_raw_rows():
    document = Document(
        original_filename="quote.pdf",
        stored_file_path="/tmp/quote.pdf",
        mime_type="application/pdf",
        line_items=[{"item_name": "정규화 품목", "quantity": 99}],
        workflow_metadata={
            "vl_candidates": [
                {
                    "tables": [
                        {
                            "table_type": "line_items",
                            "source": "paddleocrvl_official_table_html",
                            "raw_columns": ["No", "품목명", "규격/코드", "수량"],
                            "raw_rows": [["1", "HDPE 포장필름", "FILM-HDPE", "20"]],
                            "rows": [
                                {
                                    "no": 1,
                                    "item_name": "HDPE 포장필름",
                                    "document_item_code": "FILM-HDPE",
                                    "quantity": 20,
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    )

    snapshot = RawExtractionSnapshotService().build(document, source="processing_pipeline")

    assert snapshot["tables"][0]["columns"] == ["No", "품목명", "규격/코드", "수량"]
    assert snapshot["tables"][0]["rows"] == [{"No": "1", "품목명": "HDPE 포장필름", "규격/코드": "FILM-HDPE", "수량": "20"}]
    assert snapshot["tables"][0]["raw_rows"] == [["1", "HDPE 포장필름", "FILM-HDPE", "20"]]


def test_raw_extraction_key_values_are_raw_source_values_only():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl",
        document_type=DocumentType.quotation,
        document_number="DOC-003",
        currency="KRW",
        raw_text="\n".join(
            [
                "견 적 서",
                "문서번호: DOC-003                         샘플번호: 003",
                "공급자",
                "상호: (주)미래테크",
                "사업자번호: 123-45-67890",
                "담당: 김선영 / 회계팀",
                "공급받는자",
                "상호: (주)시흥대야점",
                "작성일: 2026.06.07",
                "유효기간: 견적일로부터 14일",
                "예상 합계 1,639,000",
            ]
        ),
    )

    snapshot = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {(item["key"], item["value"], item["source"]) for item in snapshot["key_values"]}

    assert ("문서번호", "DOC-003", "vl_raw_text_key_value") in values
    assert ("샘플번호", "003", "vl_raw_text_key_value") in values
    assert ("공급자 상호", "(주)미래테크", "vl_raw_text_key_value") in values
    assert ("공급자 사업자번호", "123-45-67890", "vl_raw_text_key_value") in values
    assert ("공급자 담당", "김선영 / 회계팀", "vl_raw_text_key_value") in values
    assert ("공급받는자 상호", "(주)시흥대야점", "vl_raw_text_key_value") in values
    assert ("작성일", "2026.06.07", "vl_raw_text_key_value") in values
    assert ("유효기간", "견적일로부터 14일", "vl_raw_text_key_value") in values
    assert ("예상 합계", "1,639,000", "vl_raw_text_key_value") in values
    assert all(item["source"] not in {"confirmed_document_field", "vl_structured_document"} for item in snapshot["key_values"])
    assert all("normalized_bbox" not in item and "bbox_source" not in item for item in snapshot["key_values"])


def test_raw_extraction_uses_only_vl_raw_text_key_values_even_when_ocr_differs():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl",
        raw_text="공급받는자\n상호: (주)시흥대야점\n유효기간: 견적일로부터 14일",
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="processing_pipeline",
        line_candidates=[
            {"text": "공급받는자", "x_min": 100, "y_min": 100, "x_max": 180, "y_max": 120},
            {"text": "상호: 주시홍대야점", "x_min": 100, "y_min": 130, "x_max": 260, "y_max": 150},
            {"text": "유효기간: 건격일로부터14일", "x_min": 100, "y_min": 160, "x_max": 300, "y_max": 180},
        ],
    )

    values = {item["key"]: item for item in snapshot["key_values"]}
    assert values["공급받는자 상호"]["value"] == "(주)시흥대야점"
    assert values["공급받는자 상호"]["source"] == "vl_raw_text_key_value"
    assert values["유효기간"]["value"] == "견적일로부터 14일"
    assert values["유효기간"]["source"] == "vl_raw_text_key_value"


def test_raw_extraction_builds_vl_raw_text_key_values_from_split_lines():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "공급받는자",
                "공급자",
                "상호:",
                "주시흥대야점",
                "작성일",
                "20260607",
                "상호:주미래테크",
            ]
        ),
    )

    snapshot = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item for item in snapshot["key_values"]}

    assert values["공급받는자 상호"]["value"] == "주시흥대야점"
    assert values["공급받는자 상호"]["source"] == "vl_raw_text_key_value"
    assert values["작성일"]["value"] == "20260607"
    assert values["작성일"]["source"] == "vl_raw_text_key_value"
    assert values["공급자 상호"]["value"] == "주미래테크"
    assert values["공급자 상호"]["source"] == "vl_raw_text_key_value"


def test_raw_extraction_joins_raw_text_identifier_continuation_lines():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "문서번호:DOC",
                "-003",
                "공급자",
                "상호:주미래테크",
                "샘플번호: 003",
            ]
        ),
    )

    snapshot = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item for item in snapshot["key_values"]}

    assert values["문서번호"]["value"] == "DOC-003"
    assert values["문서번호"]["source"] == "vl_raw_text_key_value"
    assert "section" not in values["문서번호"]
    assert values["샘플번호"]["value"] == "003"
    assert "section" not in values["샘플번호"]


def test_raw_extraction_builds_pos_daily_key_values_from_vl_raw_text_split_lines():
    document = Document(
        original_filename="DOC-009_pos_daily_settlement_uncropped_photo.png",
        stored_file_path="/tmp/DOC-009.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "문서번호:DOC-009",
                "POS",
                "일일정산",
                "일자: 20260620",
                "매장:",
                "가온푸드",
                "실판매금액",
                "1266000",
                "순판매금액",
                "1266000",
                "공급가액",
                "1150909",
                "VAT",
                "115091",
                "결제합계",
                "1266000",
                "매장판애",
                "15",
                "배달판마",
                "12",
            ]
        ),
    )

    snapshot = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item for item in snapshot["key_values"]}

    assert values["문서번호"]["value"] == "DOC-009"
    assert values["매장"]["value"] == "가온푸드"
    assert values["실판매금액"]["value"] == "1266000"
    assert values["공급가액"]["value"] == "1150909"
    assert values["VAT"]["value"] == "115091"
    assert values["결제합계"]["value"] == "1266000"
    assert values["매장판매"]["value"] == "15"
    assert values["배달판매"]["value"] == "12"
    assert all(item["source"] == "vl_raw_text_key_value" for item in snapshot["key_values"])


def test_semantic_mapping_uses_pos_payment_total_as_document_total():
    document = Document(
        original_filename="DOC-009_pos_daily_settlement_uncropped_photo.png",
        stored_file_path="/tmp/DOC-009.png",
        mime_type="image/png",
        document_type=DocumentType.general_document,
        category="pos_daily_settlement",
        document_number="DOC-009",
        currency="KRW",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "문서번호:DOC-009",
                "POS 일일정산",
                "일자: 20260620",
                "매장:",
                "가온푸드",
                "실판매금액",
                "1266000",
                "순판매금액",
                "1266000",
                "공급가액",
                "1150909",
                "VAT",
                "115091",
                "결제합계",
                "1266000",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert mapping["category"] == "pos_daily_settlement"
    assert fields["payment_total"] == "1266000"
    assert fields["document_total"] == "1266000"
    assert fields["supply_amount"] == "1150909"
    assert fields["vat_amount"] == "115091"
    assert fields["tax_amount"] == "115091"


def test_semantic_mapping_promotes_quotation_expected_total_and_party_sections():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        document_type=DocumentType.quotation,
        category="quotation",
        document_number="DOC-003",
        extracted_amount=Decimal("1120000"),
        currency="KRW",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "견 적 서",
                "문서번호: DOC-003                         샘플번호: 003",
                "공급자",
                "상호: (주)미래테크",
                "사업자번호: 123-45-67890",
                "담당: 김선영 / 회계팀",
                "공급받는자",
                "상호: (주)시흥대야점",
                "작성일: 2026.06.07",
                "유효기간: 견적일로부터 14일",
                "예상 합계 1,639,000",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["vendor_name"] == "(주)미래테크"
    assert fields["customer_name"] == "(주)시흥대야점"
    assert fields["issue_date"] == "2026.06.07"
    assert fields["estimated_total"] == "1639000"
    assert fields["document_total"] == "1639000"


def test_semantic_mapping_does_not_use_party_business_number_as_party_name():
    document = Document(
        original_filename="DOC-004_transaction_statement_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-004.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.transaction_statement,
        category="transaction_statement",
        vendor_name="잘못된공급자",
        customer_name="잘못된고객사",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "거래명세서",
                "공급자",
                "사업자번호: 123-45-67890",
                "상호: (주)대성정공",
                "공급받는자",
                "사업자번호: 987-65-43210",
                "상호: (주)시흥대야점",
                "거래일자: 2026.06.17",
                "공급가액 10,876,400",
                "부가세 1,087,640",
                "총합계 11,964,040",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["vendor_name"] == "(주)대성정공"
    assert fields["customer_name"] == "(주)시흥대야점"
    assert fields["issue_date"] == "2026.06.17"
    assert fields["supply_amount"] == "10876400"
    assert fields["tax_amount"] == "1087640"
    assert fields["document_total"] == "11964040"


def test_semantic_mapping_separates_sample_document_and_reference_numbers():
    document = Document(
        original_filename="DOC-001_purchase_order_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-001.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.purchase_order,
        category="purchase_order",
        document_number="DOC-001",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "발주서",
                "문서번호: PO-2026-0001",
                "샘플번호: 001",
                "참조번호: RFQ-2026-0042",
                "발행일: 2026.06.12",
                "납기일: 2026.06.30",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["sample_id"] == "001"
    assert fields["document_number"] == "PO-2026-0001"
    assert fields["reference_number"] == "RFQ-2026-0042"
    assert fields["issue_date"] == "2026.06.12"
    assert fields["due_date"] == "2026.06.30"


def test_semantic_mapping_filters_header_and_summary_rows_from_line_items():
    document = Document(
        original_filename="DOC-050_transaction_statement_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-050.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.transaction_statement,
        category="transaction_statement",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="거래명세서\n문서번호: TS-2026-0050",
    )
    raw = {
        "key_values": [{"key": "문서번호", "value": "TS-2026-0050", "source": "vl_raw_text_key_value"}],
        "tables": [
            {
                "columns": ["No", "품목명", "품목코드", "수량", "단위", "단가", "금액"],
                "rows": [
                    {"No": "No", "품목명": "품목명", "품목코드": "품목코드", "수량": "수량", "단위": "단위", "단가": "단가", "금액": "금액"},
                    {"No": "1", "품목명": "POS 영수증 용지", "품목코드": "POS-PAPER", "수량": "300", "단위": "BOX", "단가": "33,000", "금액": "9,900,000"},
                    {"No": "", "품목명": "총합계", "품목코드": "", "수량": "", "단위": "", "단가": "", "금액": "11,964,040"},
                ],
            }
        ],
    }

    mapping = SemanticMappingService().map_raw(document, raw)
    assert mapping["line_items"] == [
        {
            "line_number": "1",
            "item_name": "POS 영수증 용지",
            "item_code": "POS-PAPER",
            "quantity": "300",
            "unit": "BOX",
            "unit_price": "33000",
            "line_total": "9900000",
        }
    ]


def test_raw_key_values_prefer_later_better_party_block_and_credit_total():
    document = Document(
        original_filename="DOC-028_return_credit_uncropped_photo.pdf",
        stored_file_path="/tmp/DOC-028.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        category="return_credit",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "공급받는자",
                "공급자",
                "상호상광유동",
                "상호주세진푸드",
                "작성일:20260604",
                "원문서:INV-2026-05-128",
                "공급자",
                "상호: (주)세진푸드",
                "공급받는자",
                "상호: (주)삼광유통",
                "크레뒷합계",
                "-93680",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item["value"] for item in raw["key_values"]}
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert values["공급자 상호"] == "(주)세진푸드"
    assert values["공급받는자 상호"] == "(주)삼광유통"
    assert values["크레딧합계"] == "-93680"
    assert fields["vendor_name"] == "(주)세진푸드"
    assert fields["customer_name"] == "(주)삼광유통"
    assert fields["document_total"] == "-93680"
    assert fields["reference_number"] == "INV-2026-05-128"
    assert fields["issue_date"] == "2026-06-04"


def test_raw_key_values_assign_same_line_party_sections_in_order():
    document = Document(
        original_filename="DOC-015_transaction_statement_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-015.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.transaction_statement,
        category="transaction_statement",
        document_number="DOC-015",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "거래명세서",
                "공급자 공급받는자 상호: (주)가온물류 상호: (주)코리아팩토리 작성일: 2026.06.08",
                "문서번호: DOC-015",
                "송합계 6.609.680",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item["value"] for item in raw["key_values"]}
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert values["공급자 상호"] == "(주)가온물류"
    assert values["공급받는자 상호"] == "(주)코리아팩토리"
    assert values["작성일"] == "2026.06.08"
    assert values["총합계"] == "6.609.680"
    assert fields["vendor_name"] == "(주)가온물류"
    assert fields["customer_name"] == "(주)코리아팩토리"
    assert fields["issue_date"] == "2026.06.08"
    assert fields["document_total"] == "6609680"


def test_semantic_mapping_amount_ocr_aliases_and_multi_number_values():
    document = Document(
        original_filename="DOC-065_receipt_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-065.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.receipt,
        category="receipt",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "영수증",
                "문서번호: DOC-065",
                "공급기액 35,936",
                "사물에 3,594",
                "함계 3,594 39,530",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["supply_amount"] == "35936"
    assert fields["tax_amount"] == "3594"
    assert fields["document_total"] == "39530"


def test_raw_key_values_use_pending_and_trailing_party_sections():
    document = Document(
        original_filename="DOC-043_quotation_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-043.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.quotation,
        category="quotation",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "공급자",
                "상호주동진전자",
                "상호:",
                "주시중대야검",
                "공급자",
                "상호: (주)동진전자",
                "사업자번호: 123-45-67890",
                "담당: 김선영 / 회계팀 공급받는자",
                "상호: (주)시흥대야점",
                "작성일: 2026.06.17",
                "예상합계 943.679",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    values = {item["key"]: item["value"] for item in raw["key_values"]}
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert values["공급자 상호"] == "(주)동진전자"
    assert values["공급받는자 상호"] == "(주)시흥대야점"
    assert values["공급자 담당"] == "김선영 / 회계팀"
    assert fields["vendor_name"] == "(주)동진전자"
    assert fields["customer_name"] == "(주)시흥대야점"
    assert fields["document_total"] == "943679"


def test_semantic_mapping_repairs_krw_thousands_dot_and_ocr_date_digits():
    document = Document(
        original_filename="DOC-043_quotation_uncropped_photo.jpg",
        stored_file_path="/tmp/DOC-043.jpg",
        mime_type="image/jpeg",
        document_type=DocumentType.quotation,
        category="quotation",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "문서번호:DOC-043",
                "작성일:20260692",
                "예상합계 943.679",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["issue_date"] == "2026-06-02"
    assert fields["estimated_total"] == "943679"
    assert fields["document_total"] == "943679"


def test_semantic_mapping_repairs_invalid_ocr_month_digit():
    document = Document(
        original_filename="DOC-062_receipt_uncropped_photo.png",
        stored_file_path="/tmp/DOC-062.png",
        mime_type="image/png",
        document_type=DocumentType.receipt,
        category="receipt",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="\n".join(
            [
                "영수증번호:DOC-062",
                "일자:",
                "20268625",
                "합계 216877",
            ]
        ),
    )

    raw = RawExtractionSnapshotService().build(document, source="processing_pipeline")
    mapping = SemanticMappingService().map_raw(document, raw)
    fields = mapping["fields"]

    assert fields["issue_date"] == "2026-06-25"
    assert fields["document_total"] == "216877"


def test_raw_extraction_reviewed_key_values_can_rename_keys():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        extraction_method="paddleocr_vl_1_6_gguf_primary_reader",
        raw_text="문서번호: DOC-003",
        workflow_metadata={
            "raw_extraction": {
                "key_values": [
                    {"key": "문서번호", "value": "DOC-003", "source": "vl_raw_text_key_value"}
                ]
            }
        },
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="manual_update",
        reviewed_key_values=[
            {
                "_review_identity": "문서번호|vl_raw_text_key_value||",
                "key": "견적번호",
                "value": "QT-003",
                "source": "vl_raw_text_key_value",
            }
        ],
    )

    assert snapshot["key_values"] == [
        {"key": "견적번호", "value": "QT-003", "source": "vl_raw_text_key_value", "reviewed": True}
    ]


def test_raw_extraction_does_not_build_key_values_from_ocr_lines_without_raw_text():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        raw_text="",
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="processing_pipeline",
        line_candidates=[
            {
                "text": "문서번호: DOC-003 샘플번호: 003",
                "x_min": 100,
                "y_min": 100,
                "x_max": 500,
                "y_max": 130,
                "confidence": 0.9,
                "page": 1,
            },
            {
                "text": "공급자",
                "x_min": 100,
                "y_min": 170,
                "x_max": 160,
                "y_max": 190,
                "confidence": 0.9,
                "page": 1,
            },
            {
                "text": "상호: (주)미래테크",
                "x_min": 110,
                "y_min": 200,
                "x_max": 310,
                "y_max": 225,
                "confidence": 0.9,
                "page": 1,
            },
        ],
    )

    assert snapshot["key_values"] == []


def test_raw_extraction_does_not_build_row_key_values_from_ocr_tokens_without_raw_text():
    document = Document(
        original_filename="DOC-003_quotation_uncropped_photo.png",
        stored_file_path="/tmp/DOC-003.png",
        mime_type="image/png",
        raw_text="",
    )

    snapshot = RawExtractionSnapshotService().build(
        document,
        source="processing_pipeline",
        line_candidates=[
            {"text": "문서번호:DOC", "x_min": 100, "y_min": 100, "x_max": 190, "y_max": 120, "confidence": 0.9},
            {"text": "-003", "x_min": 192, "y_min": 99, "x_max": 230, "y_max": 119, "confidence": 0.9},
            {"text": "Quotation", "x_min": 420, "y_min": 101, "x_max": 500, "y_max": 121, "confidence": 0.9},
            {"text": "작성일", "x_min": 500, "y_min": 150, "x_max": 550, "y_max": 170, "confidence": 0.9},
            {"text": "20260607", "x_min": 552, "y_min": 149, "x_max": 630, "y_max": 169, "confidence": 0.9},
            {"text": "상호:", "x_min": 100, "y_min": 200, "x_max": 150, "y_max": 220, "confidence": 0.9},
            {"text": "(주)미래테크", "x_min": 152, "y_min": 199, "x_max": 260, "y_max": 219, "confidence": 0.9},
            {"text": "금액", "x_min": 500, "y_min": 201, "x_max": 540, "y_max": 221, "confidence": 0.9},
        ],
    )

    assert snapshot["key_values"] == []
