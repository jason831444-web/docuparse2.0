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


def test_raw_extraction_prefers_direct_vl_key_value_bbox_over_ocr_line_bbox():
    document = Document(
        original_filename="transfer.pdf",
        stored_file_path="/tmp/transfer.pdf",
        mime_type="application/pdf",
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
        if item.get("key") == "문서번호" and item.get("value") == "DOC-007"
    ]
    assert len(matches) == 1
    assert matches[0]["source"] == "vl_key_value"
    assert "normalized_bbox" not in matches[0]
    assert "key_bbox" not in matches[0]
    assert "value_bbox" not in matches[0]
    assert "bbox_source" not in matches[0]


def test_raw_extraction_keeps_plain_key_values_without_bbox_fields():
    document = Document(
        original_filename="quote.pdf",
        stored_file_path="/tmp/quote.pdf",
        mime_type="application/pdf",
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
    assert any(item["source"] == "ocr_key_value" and item["key"] == "유효기간" for item in snapshot["key_values"])


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


def test_raw_extraction_prefers_vl_raw_text_key_values_before_ocr_fallback():
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


def test_raw_extraction_ocr_key_values_keep_line_bboxes():
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

    values = {item["key"]: item for item in snapshot["key_values"]}
    assert values["문서번호"]["value"] == "DOC-003"
    assert values["문서번호"]["source"] == "ocr_key_value"
    assert "bbox_source" not in values["문서번호"]
    assert "normalized_bbox" not in values["문서번호"]
    assert "key_bbox" not in values["문서번호"]
    assert "value_bbox" not in values["문서번호"]
    assert values["샘플번호"]["value"] == "003"
    assert values["공급자 상호"]["value"] == "(주)미래테크"


def test_raw_extraction_ocr_row_key_values_merge_nearby_value_tokens():
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

    values = {item["key"]: item for item in snapshot["key_values"]}
    assert values["문서번호"]["value"] == "DOC-003"
    assert values["문서번호"]["source"] == "ocr_key_value"
    assert "bbox_source" not in values["문서번호"]
    assert values["작성일"]["value"] == "20260607"
    assert values["상호"]["value"] == "(주)미래테크"
