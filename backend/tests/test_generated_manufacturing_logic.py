from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace

from app.models.document import Document, DocumentType
from app.services.item_master_matcher import ItemMasterMatcher, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.document_taxonomy import DocumentTaxonomyService
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "samples" / "generated_logic_tests"


def _text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text()


def _masters(active_only: bool = True):
    rows, errors = parse_item_master_csv((FIXTURE_ROOT / "item_master_logic.csv").read_bytes())
    assert not errors
    if active_only:
        rows = [row for row in rows if row["active"]]
    return [SimpleNamespace(**row) for row in rows]


def _document(parsed, filename: str, text: str, *, match: bool = False) -> Document:
    line_items = parsed.line_items
    if match:
        line_items = ItemMasterMatcher().match_line_items_against_masters(line_items, _masters())
    return Document(
        original_filename=filename,
        stored_file_path=f"/tmp/{filename}",
        mime_type="text/plain",
        document_type=parsed.document_type,
        vendor_name=parsed.vendor_name,
        customer_name=parsed.customer_name,
        document_number=parsed.document_number,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        extracted_amount=parsed.extracted_amount,
        currency=parsed.currency,
        line_items=line_items,
    )


def test_due_request_date_and_comma_table_document_item_code_are_extracted():
    text = _text("purchase_order_due_request.txt")
    parsed = DocumentParser().parse(text, "generated_po.txt")
    item = parsed.line_items[0]

    assert parsed.document_type == DocumentType.purchase_order
    assert parsed.document_number == "PO-GEN-1001"
    assert parsed.issue_date.isoformat() == "2026-07-20"
    assert parsed.due_date.isoformat() == "2026-07-28"
    assert parsed.vendor_name == "대한테스트부품"
    assert parsed.customer_name == "한빛테스트제조"
    assert item["item_code"] == "CON-PCB-12P"
    assert item["document_item_code"] == "CON-PCB-12P"
    assert item["source_item_code"] == "CON-PCB-12P"
    assert item["quantity"] == 1500
    assert item["supply_amount"] == 450000
    assert item["tax_amount"] == 45000
    assert item["line_total"] == 495000


def test_tax_invoice_classification_customer_and_workflow_labels_are_invoice_specific():
    text = _text("tax_invoice_customer.txt")
    parsed = DocumentParser().parse(text, "generated_invoice.txt")
    document = _document(parsed, "generated_invoice.txt", text, match=True)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.invoice
    assert parsed.category == "invoice"
    assert parsed.document_number == "INV-GEN-2001"
    assert parsed.vendor_name == "성진테스트전자"
    assert parsed.customer_name == "네오팩토리"
    assert parsed.issue_date.isoformat() == "2026-07-21"
    assert parsed.due_date.isoformat() == "2026-08-05"
    assert parsed.line_items[0]["item_code"] == "CBL-HAR-500"
    assert document.line_items[0]["item_master_match_status"] == "alias_matched"
    assert document.line_items[0]["internal_item_code"] == "INT-CBL-500"
    assert "견적서" not in (workflow.workflow_summary or "")
    assert "견적번호" not in (workflow.workflow_summary or "")
    assert any(date_label.startswith("지급기한") for date_label in workflow.key_dates)
    assert workflow.workflow_metadata["workflow_mode"] == "invoice"
    assert workflow.workflow_metadata["document_subtype"] == "tax_invoice"
    assert workflow.workflow_metadata["document_profile"] == "tax_document"
    assert "tax_document" in workflow.workflow_metadata["document_profiles"]


def test_tax_invoice_taxonomy_uses_raw_text_when_cleaned_text_loses_header_signals():
    raw_text = "\n".join([
        "전자세금계산서",
        "승인번호 20261202-0001-ADJ",
        "계산서번호 INV-2026-1202-ADJ",
        "작성일자 2026-12-02",
        "공급자 성진전자부품",
        "공급받는자 네오팩토리",
        "No 품목명 문서품목코드 규격 수량 단위 단가 공급가액 세액 합계금액",
        "1 Cable Harness 500 CBL-HAR-500 500mm 10 EA 2200 22000 2200 24200",
        "2 PCB Connector 12P CON-PCB-12P 12P 100 EA 300 30000 3000 33000",
        "3 ROUND-ADJ 조정 - 1 EA -520 -520 -52 -572",
        "공급가액 51480",
        "세액 5148",
        "합계금액 56628",
    ])
    cleaned_text = "\n".join([
        "계산서번호 INV-2026-1202-ADJ",
        "1 Cable Harness 500 CBL-HAR-500 500mm 10 EA 2200 22000 2200 24200",
        "2 PCB Connector 12P CON-PCB-12P 12P 100 EA 300 30000 3000 33000",
        "3 ROUND-ADJ 조정 - 1 EA -520 -520 -52 -572",
        "합계금액 56628",
    ])
    parsed = DocumentParser().parse(raw_text, "synthetic_tax_invoice_negative_adjustment_variant.txt")
    document = _document(parsed, "synthetic_tax_invoice_negative_adjustment_variant.txt", raw_text)
    document.raw_text = raw_text

    workflow = DocumentWorkflowEnrichmentService().enrich(document, cleaned_text)

    assert workflow.workflow_metadata["document_subtype"] == "tax_invoice"
    assert workflow.workflow_metadata["document_profile"] == "tax_document"
    assert "tax_document" in workflow.workflow_metadata["document_profiles"]


def test_regular_us_invoice_is_not_overclassified_as_tax_invoice():
    text = "\n".join([
        "COMMERCIAL INVOICE",
        "Invoice No",
        "INV-US-2026-1001-001",
        "Vendor",
        "Global Motion Parts LLC",
        "Customer",
        "NeoFactory Korea",
        "Currency",
        "USD",
        "Total Amount",
        "650.00",
        "Item Description Vendor SKU Qty Unit Price Total",
        "Linear Guide Rail HGW20 HGW20-1000 10 45.00 450.00",
    ])
    parsed = DocumentParser().parse(text, "commercial_invoice.txt")
    document = _document(parsed, "commercial_invoice.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.invoice
    assert workflow.workflow_metadata["document_subtype"] == "commercial_invoice"
    assert workflow.workflow_metadata["document_profile"] == "foreign_currency_document"
    assert workflow.workflow_metadata["document_subtype"] != "tax_invoice"


def test_slash_separated_delivery_note_items_without_prices_are_valid():
    text = _text("delivery_note_slash_no_price.txt")
    parsed = DocumentParser().parse(text, "generated_delivery_note.txt")
    document = _document(parsed, "generated_delivery_note.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = [issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert parsed.document_type == DocumentType.delivery_note
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["item_code"] == "BRG-H-100"
    assert parsed.line_items[0]["quantity"] == 25
    assert parsed.line_items[0]["unit"] == "EA"
    assert "missing_price_or_total" not in issue_codes
    assert "missing_line_items" not in issue_codes


def test_return_credit_signals_are_preserved_as_subtype_without_delivery_note_overwrite():
    text = "\n".join([
        "반품 / 차감 요청서",
        "문서번호",
        "RTN-2026-0919-O11",
        "관련 납품서",
        "DN-2026-0914-2F",
        "공급업체",
        "대영부품",
        "고객사",
        "오성테크",
        "반품품목 규격 수량 단가 공급가액 세액 합계",
        "베어링 하우징 100mm 2 5000 10000 1000 11000",
    ])
    parsed = DocumentParser().parse(text, "return_note.txt")
    document = _document(parsed, "return_note.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.general_document
    assert parsed.document_number == "RTN-2026-0919-011"
    assert workflow.workflow_metadata["document_subtype"] in {"return_note", "credit_note"}
    assert workflow.workflow_metadata["document_profile"] == "return_document"
    assert workflow.workflow_metadata["review_required"] is True
    assert "amount_direction_requires_review" in {
        issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]
    }


def test_return_credit_with_related_document_on_next_line_keeps_return_number():
    text = "\n".join([
        "반품 / Credit Memo",
        "문서번호: RTN-2026-1204-CR",
        "공급업체: 대영부품",
        "고객사: 오성테크",
        "관련 원 납품서:",
        "DN-2026-1202-RCV",
        "No 반품품목 규격 수량 단위 단가 차감공급가액 세액 차감합계",
        "1 베어링 하우징 100mm 1 EA 8000 8000 800 8800",
        "2 S45C PIN 8X60 8x60 5 EA 600 3000 300 3300",
        "차감 합계 12,100",
    ])
    parsed = DocumentParser().parse(text, "return_credit_related_next_line_variant.txt")
    document = _document(parsed, "return_credit_related_next_line_variant.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.general_document
    assert parsed.document_number == "RTN-2026-1204-CR"
    assert parsed.business_fields["related_document_number"] == "DN-2026-1202-RCV"
    assert workflow.workflow_metadata["document_subtype"] == "credit_note"
    assert workflow.workflow_metadata["document_profile"] == "return_document"
    assert workflow.workflow_metadata["review_required"] is True


def test_internal_transfer_taxonomy_suppresses_party_and_price_blockers():
    document = Document(
        original_filename="transfer.txt",
        stored_file_path="/tmp/transfer.txt",
        mime_type="text/plain",
        document_type=DocumentType.general_document,
        document_number="TRF-2026-0922-002",
        category="internal_transfer",
        tags=["internal_transfer"],
        line_items=[
            {"item_name": "SUS304 2T PLATE", "item_code": "M-PLT-SUS304-2T-1000X2000", "quantity": 3, "unit": "EA"},
            {"item_name": "M8 육각 볼트", "item_code": "P-BOLT-M8-20-ZN", "quantity": 100, "unit": "EA"},
        ],
    )
    text = "사업장간 자재 이동 요청서\nTRF-2026-0922-002\n내부품목코드\n요청수량"
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    codes = [issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert workflow.workflow_metadata["document_subtype"] == "internal_transfer"
    assert workflow.workflow_metadata["document_profile"] == "inventory_movement_document"
    assert "no_price_document" in workflow.workflow_metadata["document_profiles"]
    assert "missing_vendor_name" not in codes
    assert "missing_customer_name" not in codes
    assert "missing_price_or_total" not in codes


def test_internal_transfer_inline_quantity_rows_are_no_price_inventory_document():
    text = "\n".join([
        "내부 자재 이동 요청서",
        "문서번호: TRF-2026-1205-003",
        "출고창고: 본사 원자재 창고",
        "입고창고: 2공장 생산라인 A",
        "No 품목명 내부품목코드 규격 요청수량 단위 비고",
        "1 SUS304 2T PLATE M-PLT-SUS304-2T-1000X2000 1000x2000 3 EA 긴급",
        "2 M8 육각 볼트 P-BOLT-M8-20-ZN M8x20 800 EA",
        "3 SUS304 평와셔 M8 P-WASH-SUS304-M8 M8 800 EA",
        "사내 이동 문서이며 거래처/금액 정보 없음",
    ])
    parsed = DocumentParser().parse(text, "internal_transfer_quantity_only_variant2.txt")
    document = _document(parsed, "internal_transfer_quantity_only_variant2.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    codes = [issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert parsed.document_type == DocumentType.general_document
    assert parsed.document_number == "TRF-2026-1205-003"
    assert parsed.extracted_amount is None
    assert parsed.currency is None
    assert len(parsed.line_items) == 3
    assert parsed.line_items[0]["item_name"] == "SUS304 2T PLATE"
    assert parsed.line_items[0]["quantity"] == 3
    assert parsed.line_items[1]["quantity"] == 800
    assert workflow.workflow_metadata["document_subtype"] == "internal_transfer"
    assert workflow.workflow_metadata["document_profile"] == "inventory_movement_document"
    assert "no_price_document" in workflow.workflow_metadata["document_profiles"]
    assert "missing_vendor_name" not in codes
    assert "missing_customer_name" not in codes
    assert "missing_price_or_total" not in codes


def test_real_delivery_note_preserves_ordered_delivered_remaining_quantities():
    text = (ROOT / "samples/pdf_samples/docuparse_realistic_manufacturing_samples/txt/14_real_delivery_note_partial_receipt_no_prices.txt").read_text()
    parsed = DocumentParser().parse(text, "14_real_delivery_note_partial_receipt_no_prices.txt")

    assert parsed.document_type == DocumentType.delivery_note
    assert parsed.extracted_amount is None
    assert parsed.currency is None
    assert len(parsed.line_items) == 4
    first, second, third, fourth = parsed.line_items
    assert first["item_name"] == "베어링 하우징"
    assert first["quantity"] == 50
    assert first["ordered_quantity"] == 80
    assert first["delivered_quantity"] == 50
    assert first["remaining_quantity"] == 30
    assert second["quantity"] == 300
    assert third["quantity"] == 1200
    assert third["ordered_quantity"] == 2000
    assert third["remaining_quantity"] == 800
    assert fourth["quantity"] == 1200
    assert all(item.get("line_total") is None for item in parsed.line_items)


def test_real_commercial_invoice_rows_keep_usd_amount_columns_without_exchange_rate_leak():
    text = (ROOT / "samples/pdf_samples/docuparse_realistic_manufacturing_samples/txt/16_real_commercial_invoice_exchange_rate.txt").read_text()
    parsed = DocumentParser().parse(text, "16_real_commercial_invoice_exchange_rate.txt")

    assert parsed.document_type == DocumentType.invoice
    assert parsed.document_number == "INV-US-2026-0916-EX"
    assert parsed.currency == "USD"
    assert parsed.extracted_amount == Decimal("650")
    assert len(parsed.line_items) == 3
    assert [item["item_code"] for item in parsed.line_items] == ["HGW20-1000", "CBL-HAR-500", "CON-PCB-12P"]
    assert [item["quantity"] for item in parsed.line_items] == [10, 50, 300]
    assert [Decimal(str(item["unit_price"])) for item in parsed.line_items] == [Decimal("45"), Decimal("2.2"), Decimal("0.3")]
    assert [Decimal(str(item["supply_amount"])) for item in parsed.line_items] == [Decimal("450"), Decimal("110"), Decimal("90")]
    assert all(item.get("line_total") is None for item in parsed.line_items)
    assert all(Decimal(str(item.get("supply_amount") or 0)) != Decimal("1370") for item in parsed.line_items)


def test_real_inspection_report_preserves_lot_and_inspection_quantities():
    text = (ROOT / "samples/pdf_samples/docuparse_realistic_manufacturing_samples/txt/18_real_incoming_inspection_report.txt").read_text()
    parsed = DocumentParser().parse(text, "18_real_incoming_inspection_report.txt")

    assert parsed.document_type == DocumentType.inspection_report
    assert parsed.extracted_amount is None
    assert parsed.currency is None
    assert len(parsed.line_items) == 2
    first, second = parsed.line_items
    assert first["item_name"] == "베어링 하우징"
    assert first["lot_no"] == "LOT-BRG-0918-A"
    assert first["specification"] == "100mm"
    assert first["quantity"] == 50
    assert first["received_quantity"] == 50
    assert first["accepted_quantity"] == 49
    assert first["rejected_quantity"] == 1
    assert first["inspection_result"] == "조건부 합격"
    assert second["item_name"] == "S45C PIN 8X60"
    assert second["quantity"] == 300
    assert second["accepted_quantity"] == 300
    assert second["rejected_quantity"] == 0


def test_real_long_invoice_suppresses_ghost_line_totals_when_supply_matches_document_total():
    text = (ROOT / "samples/pdf_samples/docuparse_realistic_manufacturing_samples/txt/20_real_invoice_multipage_many_lines.txt").read_text()
    parsed = DocumentParser().parse(text, "20_real_invoice_multipage_many_lines.txt")

    assert parsed.document_number == "INV-2026-0920-LONG"
    assert parsed.extracted_amount == Decimal("431200")
    assert len(parsed.line_items) == 15
    assert parsed.line_items[0]["quantity"] == 110
    assert parsed.line_items[0]["unit_price"] == 105
    assert parsed.line_items[0]["supply_amount"] == 11550
    assert sum(item["supply_amount"] for item in parsed.line_items) == 392000
    assert all(item.get("tax_amount") is None for item in parsed.line_items)
    assert all(item.get("line_total") is None for item in parsed.line_items)


def test_internal_transfer_pipe_table_extracts_quantity_only_rows_without_amounts():
    text = "\n".join([
        "사업장간 자재 이동 요청서",
        "문서번호",
        "TRF-2026-1104-003",
        "출고창고",
        "본사 자재창고",
        "입고창고",
        "2공장 조립라인",
        "No | 품목명 | 내부품목코드 | 규격 | 요청수량 | 단위 | 비고",
        "1 | SUS304 2T PLATE | M-PLT-SUS304-2T-1000X2000 | 1000x2000 | 4 | EA | 라인 투입",
        "2 | M8 육각 볼트 | P-BOLT-M8-20-ZN | M8x20 | 1200 | EA | 생산 보충",
        "3 | SUS WASHER M8 | P-WASH-SUS304-M8 | M8 | 1200 | EA | 생산 보충",
        "금액 정보 없음",
    ])
    parsed = DocumentParser().parse(text, "internal_transfer_variant.txt")
    document = _document(parsed, "internal_transfer_variant.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = {issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]}

    assert parsed.document_type == DocumentType.general_document
    assert parsed.document_number == "TRF-2026-1104-003"
    assert parsed.currency is None
    assert parsed.extracted_amount is None
    assert parsed.category == "internal_transfer"
    assert len(parsed.line_items) == 3
    assert [item["item_name"] for item in parsed.line_items] == [
        "SUS304 2T PLATE",
        "M8 육각 볼트",
        "SUS WASHER M8",
    ]
    assert [item["quantity"] for item in parsed.line_items] == [4, 1200, 1200]
    assert all(item.get("line_total") is None for item in parsed.line_items)
    assert workflow.workflow_metadata["document_subtype"] == "internal_transfer"
    assert "no_price_document" in workflow.workflow_metadata["document_profiles"]
    assert "missing_price_or_total" not in issue_codes
    assert "공급업체 미확인" not in (workflow.workflow_summary or "")
    assert "고객사 미확인" not in (workflow.workflow_summary or "")
    assert "합계금액 미확인" not in (workflow.workflow_summary or "")
    assert "금액/통화 정보 없이 수량 중심" in (workflow.workflow_summary or "")


def test_delivery_note_pipe_table_preserves_document_item_codes_without_amounts():
    text = "\n".join([
        "납품서",
        "문서번호",
        "DN-2026-1101-WH",
        "납품일",
        "2026-11-01",
        "공급업체",
        "대영부품",
        "고객사",
        "오성테크",
        "입고창고",
        "2공장 입고장",
        "수령자",
        "김현장",
        "No | 품목명 | 문서품목코드 | 규격 | 발주수량 | 납품수량 | 잔량 | 단위",
        "1 | 베어링 하우징 | BRG-H-100 | 100mm | 20 | 12 | 8 | EA",
        "2 | S45C PIN 8X60 | PIN-S45C-8X60 | 8x60 | 50 | 50 | 0 | EA",
        "3 | M8 볼트 SET | BOLT-SET-M8 | M8 | 100 | 80 | 20 | SET",
        "금액 정보 없음",
    ])
    parsed = DocumentParser().parse(text, "delivery_note_no_price_variant.txt")
    document = _document(parsed, "delivery_note_no_price_variant.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = {issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]}

    assert parsed.document_type == DocumentType.delivery_note
    assert parsed.document_number == "DN-2026-1101-WH"
    assert parsed.currency is None
    assert parsed.extracted_amount is None
    assert len(parsed.line_items) == 3
    assert [item["document_item_code"] for item in parsed.line_items] == [
        "BRG-H-100",
        "PIN-S45C-8X60",
        "BOLT-SET-M8",
    ]
    assert [item["quantity"] for item in parsed.line_items] == [12, 50, 80]
    assert all(item.get("line_total") is None for item in parsed.line_items)
    assert workflow.workflow_metadata.get("document_subtype") != "internal_transfer"
    assert workflow.workflow_metadata["document_profile"] == "no_price_document"
    assert "missing_document_item_code" not in issue_codes
    assert "missing_price_or_total" not in issue_codes


def test_delivery_note_with_receiving_warehouse_is_not_internal_transfer():
    text = "\n".join([
        "납품서",
        "납품번호: DN-2026-1206-004",
        "공급업체: 대영부품",
        "고객사: 오성테크",
        "입고창고: 2공장 입고장",
        "수령자: 김현장",
        "No | 품목명 | 문서품목코드 | 규격 | 발주수량 | 납품수량 | 잔량 | 단위",
        "1 | 베어링 하우징 | BRG-H-100 | 100mm | 20 | 12 | 8 | EA",
        "2 | S45C PIN 8X60 | PIN-S45C-8X60 | 8x60 | 50 | 50 | 0 | EA",
        "금액 정보 없음",
    ])

    parsed = DocumentParser().parse(text, "delivery_note_receiving_warehouse_variant.txt")
    document = _document(parsed, "delivery_note_receiving_warehouse_variant.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.delivery_note
    assert parsed.category == "delivery_note"
    assert parsed.currency is None
    assert parsed.extracted_amount is None
    assert workflow.workflow_metadata.get("document_subtype") != "internal_transfer"
    assert workflow.workflow_metadata["document_profile"] == "no_price_document"


def test_vertical_transaction_statement_table_skips_transaction_date_column():
    text = "\n".join([
        "거 래 명 세 서",
        "공급업체",
        "태성금속",
        "고객사",
        "세진기계",
        "명세서번호",
        "TS-2026-0913-MON",
        "발행일",
        "2026-09-13",
        "전월이월",
        "1,240,000",
        "No",
        "거래일",
        "품목명",
        "규격",
        "수량",
        "단위",
        "공급가액",
        "합계",
        "1",
        "09-03",
        "SUS304 3T PLATE",
        "1000x2000",
        "3",
        "EA",
        "105,000",
        "115,500",
        "2",
        "09-05",
        "AL6061 환봉 10파이",
        "3000mm",
        "12",
        "EA",
        "216,000",
        "237,600",
        "3",
        "09-08",
        "M8 육각볼트",
        "M8x20",
        "2,000",
        "EA",
        "240,000",
        "264,000",
        "4",
        "09-12",
        "SUS WASHER M8",
        "M8",
        "2,000",
        "EA",
        "80,000",
        "88,000",
        "금월 합계",
        "705,100",
        "총 미수금",
        "1,945,100",
    ])
    parsed = DocumentParser().parse(text, "transaction_statement_vertical.txt")
    document = _document(parsed, "transaction_statement_vertical.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = {issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]}

    assert parsed.document_type == DocumentType.transaction_statement
    assert parsed.document_number == "TS-2026-0913-MON"
    assert parsed.issue_date and parsed.issue_date.isoformat() == "2026-09-13"
    assert parsed.extracted_amount == Decimal("705100")
    assert len(parsed.line_items) == 4
    assert [item["item_name"] for item in parsed.line_items] == [
        "SUS304 3T PLATE",
        "AL6061 환봉 10파이",
        "M8 육각볼트",
        "SUS WASHER M8",
    ]
    assert [item["quantity"] for item in parsed.line_items] == [3, 12, 2000, 2000]
    assert [item["line_total"] for item in parsed.line_items] == [115500, 237600, 264000, 88000]
    assert "statement_balance_summary_requires_review" in issue_codes
    assert "missing_issue_date" not in issue_codes
    assert workflow.workflow_metadata["review_required"] is True


def test_return_credit_pipe_table_extracts_items_and_related_original_document():
    text = "\n".join([
        "반품 / 차감 / Credit Memo",
        "문서번호",
        "RTN-2026-1103-MIX",
        "관련 원문서",
        "DN-2026-1101-WH",
        "공급업체",
        "대영부품",
        "고객사",
        "오성테크",
        "No | 반품품목 | 규격 | 수량 | 단위 | 단가 | 공급가액 | 세액 | 차감합계",
        "1 | 베어링 하우징 | 100mm | 1 | EA | 8000 | 8000 | 800 | 8800",
        "2 | S45C PIN 8X60 | 8x60 | 5 | EA | 600 | 3000 | 300 | 3300",
        "차감 공급가액 11000",
        "차감 세액 1100",
        "차감 합계 12100",
        "금액 방향은 회계 확인 필요",
    ])
    parsed = DocumentParser().parse(text, "return_credit_variant.txt")
    document = _document(parsed, "return_credit_variant.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = {issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]}

    assert parsed.document_type == DocumentType.general_document
    assert parsed.document_number == "RTN-2026-1103-MIX"
    assert parsed.business_fields["related_document_number"] == "DN-2026-1101-WH"
    assert parsed.extracted_amount == Decimal("12100")
    assert len(parsed.line_items) == 2
    assert [item["item_name"] for item in parsed.line_items] == ["베어링 하우징", "S45C PIN 8X60"]
    assert [item["line_total"] for item in parsed.line_items] == [8800, 3300]
    assert workflow.workflow_metadata["document_subtype"] in {"return_note", "credit_note"}
    assert workflow.workflow_metadata["document_profile"] == "return_document"
    assert workflow.workflow_metadata["review_required"] is True
    assert "amount_direction_requires_review" in issue_codes
    assert "related_document_missing" not in issue_codes


def test_malformed_amounts_create_review_issue_without_corrupting_numeric_fields():
    text = _text("malformed_amounts.txt")
    parsed = DocumentParser().parse(text, "generated_malformed_amounts.txt")
    document = _document(parsed, "generated_malformed_amounts.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert parsed.line_items[0]["supply_amount"] == 3000
    assert parsed.line_items[0]["tax_amount"] == 5000
    assert parsed.line_items[0]["line_total"] == 4000
    assert all("미확인" not in str(parsed.line_items[0].get(field, "")) for field in ["item_code", "quantity", "tax_amount", "line_total"])
    assert any(issue["code"] == "invalid_line_amount" for issue in issues)
    assert workflow.workflow_metadata["review_required"] is True


def test_item_code_name_conflict_blocks_auto_ready():
    document = Document(
        original_filename="conflict_invoice.txt",
        stored_file_path="/tmp/conflict_invoice.txt",
        mime_type="text/plain",
        document_type=DocumentType.invoice,
        vendor_name="동진부품",
        customer_name="오성테크",
        document_number="INV-1",
        extracted_amount=Decimal("132000"),
        currency="KRW",
        line_items=[
            {
                "item_name": "S45C PIN 8X60",
                "internal_item_code": "P-PIN-S45C-08X60",
                "quantity": 200,
                "unit": "EA",
                "unit_price": 600,
                "line_total": 132000,
                "validation_warnings": ["item_code_name_conflict"],
            }
        ],
    )
    workflow = DocumentWorkflowEnrichmentService().enrich(document, "세금계산서")
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert any(issue["code"] == "item_code_name_conflict" for issue in issues)
    assert workflow.workflow_metadata["review_required"] is True


def test_alias_matching_ambiguous_duplicate_and_inactive_master_policy():
    matcher = ItemMasterMatcher()

    direct = matcher.match_line_items_against_masters(
        [{"item_name": "Some name", "item_code": "INT-PCB-12P", "quantity": 1, "unit": "EA", "unit_price": 300, "line_total": 300}],
        _masters(active_only=False),
    )[0]
    alias = matcher.match_line_items_against_masters(
        [{"item_name": "Cable Harness 500mm", "item_code": "CBL-HAR-500", "quantity": 1, "unit": "EA", "unit_price": 2200, "line_total": 2200}],
        _masters(active_only=False),
    )[0]
    ambiguous = matcher.match_line_items_against_masters(
        [{"item_name": "SUS-304 판재", "specification": "1000x2000", "quantity": 1, "unit": "EA", "unit_price": 25000, "line_total": 25000}],
        _masters(active_only=False),
    )[0]

    assert direct["item_master_match_status"] == "direct_code_match"
    assert direct["internal_item_code"] == "INT-PCB-12P"
    assert alias["item_master_match_status"] == "alias_matched"
    assert alias["internal_item_code"] == "INT-CBL-500"
    assert ambiguous["item_master_match_status"] == "ambiguous"
    assert ambiguous.get("internal_item_code") in (None, "")
    candidate_codes = {candidate["internal_item_code"] for candidate in ambiguous["item_master_candidates"]}
    assert "INT-INACTIVE-PLATE" not in candidate_codes


def test_missing_document_item_code_is_info_when_internal_match_is_confident_and_issues_are_deduped():
    text = _text("ambiguous_matching.txt")
    parsed = DocumentParser().parse(text, "generated_ambiguous.txt")
    item = parsed.line_items[0]
    item["internal_item_code"] = "INT-SUS304-2T-A"
    item["item_master_match_status"] = "auto_matched"
    document = _document(parsed, "generated_ambiguous.txt", text)
    document.line_items = [item]
    document.low_confidence_fields = ["missing_document_item_code:item_1", "missing_document_item_code:item_1"]

    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issues = workflow.workflow_metadata["normalized_review_issues"]
    missing_code_issues = [issue for issue in issues if issue["code"] == "missing_document_item_code"]

    assert len(missing_code_issues) == 1
    assert missing_code_issues[0]["severity"] == "info"
    assert workflow.workflow_metadata["review_required"] is False
    assert not any(issue["code"] in {"internal_item_unmatched", "internal_item_ambiguous"} for issue in issues)
