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

from app.models.document import DocumentType
from app.services.ai_escalation import should_escalate_to_ai
from app.services.file_ingestion import NormalizedDocument
from app.services.ocr_table_reconstructor import reconstruct_ocr_line_items
from app.services.parser import DocumentParser
from app.services.quality_evaluation import DocumentQualityEvaluator


def test_ocr_numeric_tail_row_becomes_line_item_not_title():
    text = "\n".join([
        "Purchase Order",
        "PO No: PO-OCR-1001",
        "Issue Date: 2026-07-21",
        "Due Delivery: 2026-07-28",
        "Supplier: Dongyang Parts",
        "Customer: Neo Factory",
        "DY S24 40x60x3T 120 EA 1600 120000 12000 132000",
    ])

    parsed = DocumentParser().parse(text, "ocr_po.pdf")

    assert parsed.document_type == DocumentType.purchase_order
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["item_name"] == "DY S24"
    assert parsed.line_items[0]["specification"] == "40x60x3T"
    assert parsed.line_items[0]["quantity"] == 120
    assert parsed.line_items[0]["line_total"] == 132000
    assert parsed.title != "DY S24 40x60x3T 120 EA 1600 120000 12000 132000"


def test_ocr_row_reconstructs_sku_spec_quantity_and_amounts():
    candidates = reconstruct_ocr_line_items([
        "1 Linear Guide Rail HGW20-1000 1000mm 8 EA 12000 96000 9600 105600",
        "S$US304 2T PLATE 1000 x 2000 7 EA 25000 175000 17500 192500",
    ])

    assert len(candidates) == 2
    first = candidates[0].item
    assert first["item_name"] == "Linear Guide Rail"
    assert first["item_code"] == "HGW20-1000"
    assert first["quantity"] == 8
    assert first["unit_price"] == 12000
    second = candidates[1].item
    assert second["item_name"] == "SUS304 2T PLATE"
    assert second["quantity"] == 7
    assert second["line_total"] == 192500


def test_ocr_delivery_note_without_prices_still_creates_line_item():
    text = "\n".join([
        "Delivery Note",
        "Delivery Note No: DN-OCR-1001",
        "Delivery Date: 2026-07-24",
        "Supplier: Motion Parts",
        "Customer: Neo Factory",
        "Bearing Housing BRG-H-100 100mm 25 EA",
    ])

    parsed = DocumentParser().parse(text, "delivery_scan.pdf")

    assert parsed.document_type == DocumentType.delivery_note
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["item_code"] == "BRG-H-100"
    assert parsed.line_items[0]["quantity"] == 25
    assert parsed.line_items[0]["unit"] == "EA"
    assert "unit_price" not in parsed.line_items[0]


def test_ocr_malformed_amount_row_keeps_numbers_and_flags_line_warning():
    text = "\n".join([
        "Purchase Order",
        "PO No: PO-OCR-2001",
        "Issue Date: 2026-07-21",
        "Due Delivery: 2026-07-28",
        "Supplier: Metal Hub",
        "Customer: Neo Factory",
        "SUS316 PLATE 2T 1000x2000 1 EA 42000 4200 46200 4200",
        "Grand Total: 46200",
    ])

    parsed = DocumentParser().parse(text, "malformed_ocr.pdf")

    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["unit_price"] == 42000
    assert parsed.line_items[0]["supply_amount"] == 4200
    assert parsed.line_items[0]["tax_amount"] == 46200
    assert parsed.line_items[0]["line_total"] == 4200
    assert "invalid_tax_greater_than_total" in parsed.line_items[0]["validation_warnings"]


def test_ai_escalates_when_ocr_has_item_like_rows_but_parser_result_has_no_items():
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text="Bearing Housing BRG-H-100 100mm 25 EA",
        raw_extracted_blocks=[{"type": "ocr", "content": "Bearing Housing BRG-H-100 100mm 25 EA"}],
        ocr_confidence=0.76,
        file_metadata={"table_confidence": 0.44},
    )
    parsed = DocumentParser().parse("Delivery Note\nDelivery Note No: DN-1", "broken.pdf")
    parsed.line_items = []
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)

    decision = should_escalate_to_ai(normalized, parsed, quality)

    assert decision.should_escalate is True
    assert "ocr_line_item_candidates_not_parsed" in decision.reasons
    assert decision.signals["ocr_line_item_candidate_count"] == 1
