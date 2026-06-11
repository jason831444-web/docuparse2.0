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
    assert second["item_name"] == "SUS304 철판 3T"
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


def test_vertical_paddleocr_invoice_vendor_sku_table_reconstructs_document_item_codes():
    text = "\n".join([
        "세금계산서/INVOICE",
        "Vendor SKU column must not become item rows",
        "계산서번호",
        "INV-2026-0803-332",
        "발행일",
        "2026-08-03",
        "공급업체",
        "성진전자부품",
        "고객사",
        "네오팩토리",
        "지급기한",
        "2026-09-02",
        "통화",
        "KRW",
        "Line",
        "Item Description",
        "Vendor SKU",
        "Specification",
        "Qty",
        "Unit",
        "Unit Price",
        "Subtotal",
        "Tax",
        "Total",
        "1",
        "PCB Connector 12F",
        "CON-PCB-12F",
        "12",
        "1500",
        "EA",
        "300",
        "450000",
        "45000",
        "495000 하네스500m",
        "2",
        "CBL-HAR-50C",
        "500mm",
        "350",
        "EA",
        "2200",
        "770000",
        "77000 847000 AL6061환봉10파이",
        "3",
        "10mm X 3000",
        "30",
        "EA",
        "8000",
        "240000",
        "24000",
        "264000",
        "공급가액:",
        "1460000",
        "부가세:",
        "146000",
        "합계금액:",
        "1606000",
    ])

    parsed = DocumentParser().parse(text, "03_image_invoice_vendor_sku.pdf")

    assert parsed.document_type == DocumentType.invoice
    assert parsed.vendor_name == "성진전자부품"
    assert parsed.customer_name == "네오팩토리"
    assert parsed.document_number == "INV-2026-0803-332"
    assert parsed.issue_date.isoformat() == "2026-08-03"
    assert parsed.due_date.isoformat() == "2026-09-02"
    assert parsed.subtotal == 1460000
    assert parsed.tax == 146000
    assert parsed.extracted_amount == 1606000
    assert len(parsed.line_items) == 3
    assert sum(item["supply_amount"] for item in parsed.line_items) == 1460000
    assert sum(item["tax_amount"] for item in parsed.line_items) == 146000
    assert sum(item["line_total"] for item in parsed.line_items) == 1606000

    first = parsed.line_items[0]
    assert first["item_name"] == "PCB Connector 12F"
    assert first["item_code"] == "CON-PCB-12F"
    assert first["document_item_code"] == "CON-PCB-12F"
    assert first["specification"] == "12"
    assert first["quantity"] == 1500
    assert first["unit"] == "EA"
    assert first["unit_price"] == 300
    assert first["supply_amount"] == 450000
    assert first["tax_amount"] == 45000
    assert first["line_total"] == 495000

    second = parsed.line_items[1]
    assert second["item_name"] == "하네스500m"
    assert "495000" not in second["item_name"]
    assert second["item_code"] == "CBL-HAR-50C"
    assert second["specification"] == "500mm"
    assert second["quantity"] == 350
    assert second["unit"] == "EA"
    assert second["unit_price"] == 2200
    assert second["supply_amount"] == 770000
    assert second["tax_amount"] == 77000
    assert second["line_total"] == 847000

    third = parsed.line_items[2]
    assert third["item_name"] == "AL6061 환봉10 파이"
    assert "77000" not in third["item_name"]
    assert "847000" not in third["item_name"]
    assert "item_code" not in third
    assert third["specification"] == "10mm X 3000"
    assert third["quantity"] == 30
    assert third["unit_price"] == 8000
    assert third["supply_amount"] == 240000
    assert third["tax_amount"] == 24000
    assert third["line_total"] == 264000


def test_vertical_paddleocr_delivery_note_no_price_table_reconstructs_quantities_without_amounts():
    text = "\n".join([
        "납품서",
        "Delivery Note",
        "no price columns",
        "납품번호",
        "DN-2026-0804-055",
        "발행일",
        "2026-08-04",
        "납품일",
        "2026-08-05",
        "공급업체",
        "대영부품",
        "고객사",
        "오성테크",
        "입고장소",
        "오성테크2공장",
        "자재창고",
        "수령자",
        "박성호",
        "차량번호",
        "서울85가2311",
        "품목명",
        "문서품목코드",
        "규격",
        "납품수량",
        "단위",
        "비고",
        "베어링",
        "하우징",
        "BRG-H-1OO",
        "100mm",
        "25",
        "EA",
        "S45C PIN 8X60",
        "PIN-S45C-08",
        "8X60",
        "500",
        "EA",
        "육각볼트",
        "M8x20",
        "BOLT-M8-20",
        "M8x20",
        "T000",
        "가",
        "SUS WASHER M8",
        "WASH-SUS-O8",
        "M8",
        "1000",
        "-",
        "본 납품서는 단가/금액 없이 입고수량 확인용으로 발행되었습니다.",
    ])

    parsed = DocumentParser().parse(text, "04_image_delivery_note_no_prices.pdf")

    assert parsed.document_type == DocumentType.delivery_note
    assert parsed.vendor_name == "대영부품"
    assert parsed.customer_name == "오성테크"
    assert parsed.document_number == "DN-2026-0804-055"
    assert parsed.issue_date.isoformat() == "2026-08-04"
    assert parsed.due_date.isoformat() == "2026-08-05"
    assert parsed.subtotal is None
    assert parsed.tax is None
    assert parsed.extracted_amount is None
    assert len(parsed.line_items) == 4

    first = parsed.line_items[0]
    assert first["item_name"] == "베어링 하우징"
    assert first["item_code"] == "BRG-H-100"
    assert first["specification"] == "100mm"
    assert first["quantity"] == 25
    assert first["unit"] == "EA"

    second = parsed.line_items[1]
    assert second["item_name"] == "S45C PIN 8X60"
    assert second["item_code"] == "PIN-S45C-08"
    assert second["specification"] == "8x60"
    assert second["quantity"] == 500
    assert second["unit"] == "EA"

    third = parsed.line_items[2]
    assert third["item_name"] == "육각볼트 M8x20"
    assert third["item_code"] == "BOLT-M8-20"
    assert third["specification"] == "M8x20"
    assert third["quantity"] == 1000
    assert third["unit"] == "EA"

    fourth = parsed.line_items[3]
    assert fourth["item_name"] == "SUS WASHER M8"
    assert fourth["item_code"] == "WASH-SUS-08"
    assert fourth["specification"] == "M8"
    assert fourth["quantity"] == 1000
    assert fourth["unit"] == "EA"


def test_vertical_paddleocr_transaction_statement_uses_amount_arithmetic_for_similar_rows():
    text = "\n".join([
        "거래명세서",
        "거래명세서번호",
        "[S-2026-0805-451",
        "거래일자",
        "2026-08-05",
        "공급업체",
        "태성금속",
        "고객사",
        "세진기계",
        "품목명",
        "규격",
        "수량",
        "단위",
        "단가",
        "공급가액",
        "세액",
        "합계금액",
        "1",
        "SUS304 철판 2T",
        "1000x2000",
        "4",
        "EA",
        "25000",
        "100000",
        "10000",
        "110000",
        "2",
        "SUS 304",
        "판2OT",
        "1000x2000",
        "6",
        "EA",
        "25000",
        "150000",
        "15000",
        "165000",
        "3",
        "알루미늄",
        "원형봉",
        "10mm",
        "3000mm",
        "20",
        "EA",
        "800C",
        "160000",
        "16000",
        "176000",
        "4",
        "M8 HEX",
        "BOLT20",
        "M8x20",
        "500C",
        "EA",
        "120",
        "5000L",
        "6000",
        "56006",
        "총액:",
        "517000",
    ])

    parsed = DocumentParser().parse(text, "05_image_transaction_statement_similar_lines.pdf")

    assert parsed.document_type == DocumentType.transaction_statement
    assert parsed.vendor_name == "태성금속"
    assert parsed.customer_name == "세진기계"
    assert parsed.document_number == "TS-2026-0805-451"
    assert parsed.issue_date.isoformat() == "2026-08-05"
    assert parsed.extracted_amount == 517000
    assert len(parsed.line_items) == 4
    assert sum(item["line_total"] for item in parsed.line_items) == 517000

    first = parsed.line_items[0]
    assert first["item_name"] == "SUS304 철판 2T"
    assert first["specification"] == "1000x2000"
    assert first["quantity"] == 4
    assert first["unit_price"] == 25000
    assert first["supply_amount"] == 100000
    assert first["tax_amount"] == 10000
    assert first["line_total"] == 110000

    second = parsed.line_items[1]
    assert second["item_name"] == "SUS304 판 2.0T"
    assert second["specification"] == "1000x2000"
    assert second["quantity"] == 6
    assert second["unit_price"] == 25000
    assert second["supply_amount"] == 150000
    assert second["tax_amount"] == 15000
    assert second["line_total"] == 165000

    third = parsed.line_items[2]
    assert third["item_name"] == "알루미늄 원형봉"
    assert third["specification"] == "10mm x 3000mm"
    assert third["quantity"] == 20
    assert third["unit_price"] == 8000
    assert third["supply_amount"] == 160000
    assert third["tax_amount"] == 16000
    assert third["line_total"] == 176000

    fourth = parsed.line_items[3]
    assert fourth["item_name"] == "M8 HEX BOLT20"
    assert fourth["specification"] == "M8x20"
    assert fourth["quantity"] == 500
    assert fourth["unit_price"] == 120
    assert fourth["supply_amount"] == 60000
    assert fourth["tax_amount"] == 6000
    assert fourth["line_total"] == 66000


def test_vertical_paddleocr_usd_invoice_keeps_full_document_number_and_line_items():
    text = "\n".join([
        "INVOICE",
        "Invoice No: INV-US-2026-0806-USD-204",
        "Issue Date",
        "2026-08-06",
        "Vendor",
        "Global Motion Parts LLC",
        "Customer",
        "NeoFactory Korea",
        "Due Date",
        "2026-09-05",
        "Currency",
        "USD",
        "Item Description",
        "Vendor SKU",
        "Specification",
        "Qty",
        "Unit",
        "Unit Price",
        "Subtotal",
        "Tax",
        "Total",
        "Linear Guide Rail",
        "HGW20-1000",
        "1000mm",
        "8",
        "EA",
        "12.50",
        "100.00",
        "10.00",
        "110.00",
        "Servo Cable 500mm",
        "CBL-SER-500",
        "500mm",
        "4",
        "EA",
        "25.00",
        "100.00",
        "10.00",
        "110.00",
        "Amount Due:",
        "220.00",
    ])

    parsed = DocumentParser().parse(text, "06_image_invoice_usd_vendor_sku.pdf")

    assert parsed.document_type == DocumentType.invoice
    assert parsed.document_number == "INV-US-2026-0806-USD-204"
    assert parsed.currency == "USD"
    assert parsed.extracted_amount == 220
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["item_code"] == "HGW20-1000"
    assert parsed.line_items[0]["quantity"] == 8
    assert parsed.line_items[0]["unit_price"] == 12.5
    assert parsed.line_items[1]["item_code"] == "CBL-SER-500"
    assert sum(item["line_total"] for item in parsed.line_items) == 220


def test_vertical_paddleocr_missing_quantity_preserves_reviewable_amount_row():
    text = "\n".join([
        "견적서",
        "견적번호",
        "OT-2026-0808-009",
        "견적일",
        "2026-08-08",
        "품목명",
        "규격",
        "수량",
        "단위",
        "단가",
        "공급가액",
        "세액",
        "합계금액",
        "고정 브라켓",
        "50x80x3T",
        "EA",
        "1500",
        "75000",
        "7500",
        "82500",
        "총액:",
        "82500",
    ])

    parsed = DocumentParser().parse(text, "08_image_quote_missing_quantity.pdf")

    assert parsed.document_type == DocumentType.quotation
    assert parsed.document_number == "QT-2026-0808-009"
    assert parsed.extracted_amount == 82500
    assert len(parsed.line_items) == 1
    item = parsed.line_items[0]
    assert item["item_name"] == "고정 브라켓"
    assert item["specification"] == "50x80x3T"
    assert "quantity" not in item
    assert item["unit"] == "EA"
    assert item["unit_price"] == 1500
    assert item["supply_amount"] == 75000
    assert item["tax_amount"] == 7500
    assert item["line_total"] == 82500


def test_vertical_paddleocr_leaked_amount_prefix_is_not_item_name():
    text = "\n".join([
        "발주서",
        "발주번호",
        "PO-2026-0807",
        "품목명",
        "품목코드",
        "규격",
        "수량",
        "단위",
        "단가",
        "공급가액",
        "세액",
        "합계금액",
        "SUS316 PLATE 2T",
        "PLT-SUS316-2T",
        "1000x2000",
        "1",
        "EA",
        "42000",
        "4200",
        "46200",
        "4200 46200 4200 고정 브라켓",
        "BRK-SUS-01",
        "50x80x3T",
        "10",
        "EA",
        "1500",
        "15000",
        "1500",
        "16500",
        "총액:",
        "20700",
    ])

    parsed = DocumentParser().parse(text, "07_image_po_malformed_amount_columns.pdf")

    assert len(parsed.line_items) >= 2
    second = parsed.line_items[1]
    assert second["item_name"] == "고정 브라켓"
    assert "4200" not in second["item_name"]
    assert second["item_code"] == "BRK-SUS-01"
    assert second["line_total"] == 16500


def test_vertical_paddleocr_supply_total_header_and_prefix_noise_do_not_create_fake_quantity():
    text = "\n".join([
        "INVOICE",
        "Invoice No",
        "INV-US-2026-0809-100",
        "Item Description",
        "Vendor SKU",
        "Specification",
        "Qty",
        "Unit",
        "Unit Price",
        "Supply Total",
        "Tax",
        "TOTAL",
        "M8 bolt",
        "BOLT-M8-20",
        "M8x20",
        "1200",
        "EA",
        "120",
        "144000",
        "14400",
        "158400 SUS washer m8",
        "WASH-SUS-08",
        "M8",
        "1200",
        "EA",
        "40",
        "48000",
        "4800",
        "52800",
        "TOTAL:",
        "211200",
    ])

    parsed = DocumentParser().parse(text, "09_image_po_mixed_korean_english_noise.pdf")

    assert parsed.document_number == "INV-US-2026-0809-100"
    assert parsed.extracted_amount == 211200
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["quantity"] == 1200
    assert parsed.line_items[0]["line_total"] == 158400
    assert parsed.line_items[1]["item_name"] == "SUS washer m8"
    assert "14400" not in parsed.line_items[1]["item_name"]
    assert "158400" not in parsed.line_items[1]["item_name"]
    assert parsed.line_items[1]["quantity"] == 1200
    assert parsed.line_items[1]["unit_price"] == 40
    assert parsed.line_items[1]["line_total"] == 52800


def test_vertical_paddleocr_poor_ocr_keeps_incomplete_line_item_candidate():
    text = "\n".join([
        "INVOICE",
        "Invoice No",
        "INV-POOR-2026-0810",
        "Item Description",
        "Vendor SKU",
        "Specification",
        "Qty",
        "Unit",
        "Unit Price",
        "Subtotal",
        "Tax",
        "Total",
        "Linear Guide Rail",
        "HGW20-1000",
        "1000mm",
        "EA",
        "12000",
        "96000",
        "9600",
        "105600",
        "Grand Total:",
        "105600",
    ])

    parsed = DocumentParser().parse(text, "10_image_poor_ocr_distorted_invoice.pdf")

    assert parsed.document_type == DocumentType.invoice
    assert parsed.extracted_amount == 105600
    assert len(parsed.line_items) == 1
    item = parsed.line_items[0]
    assert item["item_name"] == "Linear Guide Rail"
    assert item["item_code"] == "HGW20-1000"
    assert item["specification"] == "1000mm"
    assert "quantity" not in item
    assert item["unit"] == "EA"
    assert item["unit_price"] == 12000
    assert item["line_total"] == 105600
