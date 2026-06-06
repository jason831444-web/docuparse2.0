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


def test_vertical_paddleocr_purchase_order_table_reconstructs_items_and_totals():
    text = "\n".join([
        "Page 1",
        "발주서",
        "Purchase",
        "Order",
        "발주번호",
        "PO-2026-0801",
        "-101",
        "발행일",
        "2026-08-01",
        "공급업체",
        "대한정밀부품",
        "고객사",
        "한빛제조",
        "납기요청일",
        "2026-08-14",
        "통화",
        "KRW",
        "승인",
        "2026",
        "품목명",
        "품목코드",
        "규격",
        "수량",
        "단위",
        "단가",
        "공급가액",
        "세액",
        "합겨",
        "SUS304철판 2T",
        "PLT-SUS304-2T",
        "1000x2000",
        "5A",
        "25000",
        "300000",
        "30000",
        "330000",
        "M8육각볼트",
        "20mm",
        "BOLT-M8-20",
        "M8x20",
        "80C",
        "EA",
        "96000",
        "9600",
        "105600",
        "SUS WASHER M8",
        "WASH-SUS-08",
        "M&",
        "80C",
        "32000",
        "320",
        "35200",
        "고정판",
        "120X605]",
        "PLT-FIX-O2",
        "120X60X57",
        "80C",
        "112000",
        "11200",
        "123200",
        "공급가액합계:",
        "540000",
        "부가세:",
        "54000",
        "총액:",
        "594000",
    ])

    parsed = DocumentParser().parse(text, "01_image_po_clean_korean.pdf")

    assert parsed.document_type == DocumentType.purchase_order
    assert parsed.document_number == "PO-2026-0801"
    assert parsed.vendor_name == "대한정밀부품"
    assert parsed.customer_name == "한빛제조"
    assert parsed.issue_date.isoformat() == "2026-08-01"
    assert parsed.due_date.isoformat() == "2026-08-14"
    assert parsed.currency == "KRW"
    assert parsed.subtotal == 540000
    assert parsed.tax == 54000
    assert parsed.extracted_amount == 594000
    assert len(parsed.line_items) == 4

    first = parsed.line_items[0]
    assert first["item_code"] == "PLT-SUS304-2T"
    assert first["quantity"] == 12
    assert first["unit"] == "EA"
    assert first["unit_price"] == 25000
    assert first["supply_amount"] == 300000
    assert first["tax_amount"] == 30000
    assert first["line_total"] == 330000

    assert parsed.line_items[1]["item_code"] == "BOLT-M8-20"
    assert parsed.line_items[1]["quantity"] == 800
    assert parsed.line_items[1]["unit_price"] == 120
    assert parsed.line_items[2]["item_code"] == "WASH-SUS-08"
    assert parsed.line_items[2]["specification"] == "M8"
    assert parsed.line_items[2]["quantity"] == 800
    assert parsed.line_items[2]["tax_amount"] == 3200
    assert parsed.line_items[3]["item_code"] == "PLT-FIX-02"
    assert parsed.line_items[3]["specification"] == "120x60x5T"
    assert parsed.line_items[3]["quantity"] == 40
    assert parsed.line_items[3]["unit"] == "EA"
    assert parsed.line_items[3]["unit_price"] == 2800
    assert parsed.line_items[3]["supply_amount"] == 112000
    assert parsed.line_items[3]["tax_amount"] == 11200
    assert parsed.line_items[3]["line_total"] == 123200


def test_vertical_paddleocr_quotation_without_item_codes_reconstructs_amount_rows():
    text = "\n".join([
        "견적서",
        "견적번호",
        "QT-2026-0802",
        "견적일",
        "2026-08-02",
        "유효기간",
        "2026-08-31",
        "공급업체",
        "대한정밀부품",
        "고객사",
        "한빛제조",
        "품목명",
        "규격",
        "수링",
        "단위",
        "단가",
        "공급가액",
        "세악",
        "합계금액",
        "스텍판2T",
        "1000X2000",
        "25001",
        "25000",
        "12500",
        "137500",
        "SUS304",
        "철판37",
        "[00OX20O0",
        "3700[",
        "148000",
        "14800",
        "162800",
        "고정",
        "플레이트",
        "120x60X51",
        "280C",
        "168000",
        "16800",
        "184800",
        "공급가액합계:441000",
        "부가세:44100",
        "총액:",
        "485100",
    ])

    parsed = DocumentParser().parse(text, "02_image_quotation_ambiguous_stainless.pdf")

    assert parsed.document_type == DocumentType.quotation
    assert parsed.subtotal == 441000
    assert parsed.tax == 44100
    assert parsed.extracted_amount == 485100
    assert len(parsed.line_items) == 3

    first = parsed.line_items[0]
    assert first["item_name"] == "스텐판 2T"
    assert first["specification"] == "1000x2000"
    assert first["quantity"] == 5
    assert first["unit"] == "EA"
    assert first["unit_price"] == 25000
    assert first["supply_amount"] == 125000
    assert first["tax_amount"] == 12500
    assert first["line_total"] == 137500

    second = parsed.line_items[1]
    assert second["item_name"] == "SUS 304 철판 3T"
    assert second["specification"] == "1000x2000"
    assert second["quantity"] == 4
    assert second["unit_price"] == 37000
    assert second["supply_amount"] == 148000
    assert second["tax_amount"] == 14800
    assert second["line_total"] == 162800

    third = parsed.line_items[2]
    assert third["item_name"] == "고정 플레이트"
    assert third["specification"] == "120x60x5T"
    assert third["quantity"] == 60
    assert third["unit_price"] == 2800
    assert third["supply_amount"] == 168000
    assert third["tax_amount"] == 16800
    assert third["line_total"] == 184800
