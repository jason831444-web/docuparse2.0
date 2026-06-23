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
    assert matches[0]["source"] == "vl_direct_key_value_bbox"
    assert matches[0]["normalized_bbox"] == [0.1, 0.1, 0.3, 0.13]
    assert matches[0]["key_bbox"] == [0.1, 0.1, 0.18, 0.13]
    assert matches[0]["value_bbox"] == [0.19, 0.1, 0.3, 0.13]


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
