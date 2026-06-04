from app.models.document import Document, DocumentType
from app.services.category_interpretation import CategoryInterpretationService
from app.services.category_taxonomy import normalize_category_value
from app.services.parser import DocumentParser
from app.services.item_master_matcher import ItemMasterMatcher
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


def test_installation_guide_beats_person_name_profile_signal():
    text = """
    Sihoon
    Project Setup Guide
    Installation
    Prerequisites
    Docker
    Environment Variables
    Database
    Run this command to start the API server.
    """

    parsed = DocumentParser().parse(text, "project_setup.pdf")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="project_setup.pdf",
            stored_file_path="/tmp/project_setup.pdf",
            mime_type="application/pdf",
            document_type=parsed.document_type,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "installation_guide"
    assert interpretation.profile == "installation_guide"
    assert interpretation.category == "installation_guide"
    assert "setup" in (interpretation.title_hint or "").lower()


def test_spreadsheet_tracker_beats_profile_token():
    text = """
    Sheet: Implementation Schedule
    Feature | Task | Status | Claimed | Testing | Coverage | Pipeline
    Student profile API (/students/{id}) | Implement endpoint | In Progress | Alex | API tests | 80% | Passing
    Search filters | Verify category consistency | Open | Sam | Regression tests | 70% | Pending
    """

    parsed = DocumentParser().parse(text, "implementation_schedule.xlsx")
    interpretation = CategoryInterpretationService().interpret(
        Document(
            original_filename="implementation_schedule.xlsx",
            stored_file_path="/tmp/implementation_schedule.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            source_file_type="xlsx",
            document_type=DocumentType.document,
            title=parsed.title,
            category=parsed.category,
        ),
        text,
    )

    assert parsed.category == "implementation_schedule"
    assert interpretation.profile == "implementation_schedule"
    assert interpretation.category == "implementation_schedule"
    assert "schedule" in (interpretation.title_hint or "").lower()


def test_category_normalization_keeps_edit_and_filter_values_consistent():
    assert normalize_category_value("Engineering Planning") == "implementation_schedule"
    assert normalize_category_value("Project Tracker") == "implementation_schedule"
    assert normalize_category_value("parent>Setup Guide") == "installation_guide"


def test_purchase_order_key_value_line_item_block_is_extracted():
    text = """
    발주서

    공급업체: 대한정밀부품
    고객사: 한빛제조
    발주번호: PO-2026-0603
    발행일: 2026-06-03
    납기일: 2026-06-10

    품목명: M8 육각 볼트
    품목코드: BOLT-M8-20
    규격: M8x20
    수량: 500
    단위: EA
    단가: 120
    공급가액: 60000
    세액: 6000
    합계금액: 66000
    """

    parsed = DocumentParser().parse(text, "sample_po.txt")

    assert parsed.document_type == DocumentType.purchase_order
    assert parsed.vendor_name == "대한정밀부품"
    assert parsed.customer_name == "한빛제조"
    assert parsed.document_number == "PO-2026-0603"
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["item_name"] == "M8 육각 볼트"
    assert parsed.line_items[0]["item_code"] == "BOLT-M8-20"
    assert parsed.line_items[0]["quantity"] == 500
    assert parsed.line_items[0]["unit_price"] == 120
    assert parsed.line_items[0]["line_total"] == 66000


def test_purchase_order_table_line_item_row_is_extracted():
    text = """
    발주서
    공급업체: 대한정밀부품
    고객사: 한빛제조
    발주번호: PO-2026-0603
    품목명 | 품목코드 | 규격 | 수량 | 단위 | 단가 | 공급가액 | 세액 | 합계금액
    M8 육각 볼트 | BOLT-M8-20 | M8x20 | 500 | EA | 120 | 60000 | 6000 | 66000
    """

    parsed = DocumentParser().parse(text, "sample_po.txt")

    assert parsed.document_type == DocumentType.purchase_order
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["specification"] == "M8x20"
    assert parsed.line_items[0]["unit"] == "EA"
    assert parsed.line_items[0]["supply_amount"] == 60000
    assert parsed.line_items[0]["tax_amount"] == 6000


def test_manufacturing_sample_parser_regression_cases():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "samples" / "manufacturing"
    parser = DocumentParser()

    expectations = {
        "01_purchase_order_basic.txt": (DocumentType.purchase_order, 2, 88000, []),
        "02_purchase_order_key_value.txt": (DocumentType.purchase_order, 1, 66000, []),
        "03_quotation.txt": (DocumentType.quotation, 2, 638000, []),
        "04_transaction_statement.txt": (DocumentType.transaction_statement, 3, 517000, []),
        "05_delivery_note.txt": (DocumentType.delivery_note, 2, 511500, []),
        "06_tax_invoice.txt": (DocumentType.invoice, 2, 814000, []),
        "07_amount_mismatch_needs_review.txt": (DocumentType.purchase_order, 2, 250000, []),
        "08_missing_quantity_needs_review.txt": (DocumentType.quotation, 1, 308000, []),
        "09_item_matching_ambiguous.txt": (DocumentType.purchase_order, 3, 509300, []),
    }

    parsed_by_name = {}
    for filename, (document_type, item_count, total_amount, _) in expectations.items():
        parsed = parser.parse((root / filename).read_text(), filename)
        parsed_by_name[filename] = parsed
        assert parsed.document_type == document_type
        assert len(parsed.line_items) == item_count
        assert int(parsed.extracted_amount) == total_amount

    quotation = parsed_by_name["03_quotation.txt"]
    assert quotation.business_fields["valid_until"] == "2026-06-20"
    assert quotation.business_fields["delivery_terms"] == "발주 후 7일 이내"
    assert quotation.business_fields["payment_terms"] == "월말 마감 후 익월 10일 현금 결제"

    delivery_note = parsed_by_name["05_delivery_note.txt"]
    assert delivery_note.business_fields["delivery_date"] == "2026-06-07"
    assert delivery_note.business_fields["receiving_location"] == "오성테크 2공장 자재창고"
    assert delivery_note.business_fields["receiver_name"] == "박성호"

    tax_invoice = parsed_by_name["06_tax_invoice.txt"]
    assert tax_invoice.business_fields["payment_due_date"] == "2026-07-07"
    assert tax_invoice.business_fields["business_registration_numbers"] == ["123-45-67890", "987-65-43210"]

    missing_quantity_item = parsed_by_name["08_missing_quantity_needs_review.txt"].line_items[0]
    assert missing_quantity_item.get("quantity") is None
    assert missing_quantity_item["unit"] == "EA"
    assert missing_quantity_item["unit_price"] == 2800
    assert missing_quantity_item["supply_amount"] == 280000
    assert missing_quantity_item["tax_amount"] == 28000
    assert missing_quantity_item["line_total"] == 308000

    ambiguous_items = parsed_by_name["09_item_matching_ambiguous.txt"].line_items
    assert [item.get("quantity") for item in ambiguous_items] == [10, 5, 3]
    assert ambiguous_items[0].get("item_code") is None
    assert ambiguous_items[0].get("specification") is None


def test_manufacturing_workflow_uses_document_type_specific_review_reasons():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "samples" / "manufacturing"
    parser = DocumentParser()
    service = DocumentWorkflowEnrichmentService()

    ready_quotation_text = (root / "03_quotation.txt").read_text()
    ready_quotation = parser.parse(ready_quotation_text, "03_quotation.txt")
    quotation_document = Document(
        original_filename="03_quotation.txt",
        stored_file_path="/tmp/03_quotation.txt",
        mime_type="text/plain",
        document_type=ready_quotation.document_type,
        vendor_name=ready_quotation.vendor_name,
        customer_name=ready_quotation.customer_name,
        document_number=ready_quotation.document_number,
        issue_date=ready_quotation.issue_date,
        due_date=ready_quotation.due_date,
        extracted_amount=ready_quotation.extracted_amount,
        currency=ready_quotation.currency,
        line_items=ready_quotation.line_items,
    )
    quotation_workflow = service.enrich(quotation_document, ready_quotation_text)
    assert quotation_workflow.warnings == []
    assert "납기일은 미확인" not in (quotation_workflow.workflow_summary or "")
    assert quotation_workflow.workflow_metadata["business_fields"]["valid_until"] == "2026-06-20"

    missing_quantity_text = (root / "08_missing_quantity_needs_review.txt").read_text()
    missing_quantity = parser.parse(missing_quantity_text, "08_missing_quantity_needs_review.txt")
    missing_quantity_document = Document(
        original_filename="08_missing_quantity_needs_review.txt",
        stored_file_path="/tmp/08_missing_quantity_needs_review.txt",
        mime_type="text/plain",
        document_type=missing_quantity.document_type,
        vendor_name=missing_quantity.vendor_name,
        customer_name=missing_quantity.customer_name,
        document_number=missing_quantity.document_number,
        issue_date=missing_quantity.issue_date,
        due_date=missing_quantity.due_date,
        extracted_amount=missing_quantity.extracted_amount,
        currency=missing_quantity.currency,
        line_items=missing_quantity.line_items,
    )
    missing_quantity_workflow = service.enrich(missing_quantity_document, missing_quantity_text)
    codes = [reason["code"] for reason in missing_quantity_workflow.workflow_metadata["review_reasons"]]
    assert "missing_quantity" in codes
    assert "missing_due_date" not in codes
    assert "missing_line_items" not in codes


def test_manufacturing_role_dates_are_normalized_for_review_forms():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "samples" / "manufacturing"
    parser = DocumentParser()

    quotation = parser.parse((root / "03_quotation.txt").read_text(), "03_quotation.txt")
    assert quotation.issue_date.isoformat() == "2026-06-04"
    assert quotation.due_date.isoformat() == "2026-06-20"
    assert quotation.business_fields["valid_until"] == "2026-06-20"

    transaction = parser.parse((root / "04_transaction_statement.txt").read_text(), "04_transaction_statement.txt")
    assert transaction.issue_date.isoformat() == "2026-06-05"
    assert transaction.business_fields["transaction_date"] == "2026-06-05"
    assert transaction.due_date is None

    delivery_note = parser.parse((root / "05_delivery_note.txt").read_text(), "05_delivery_note.txt")
    assert delivery_note.issue_date.isoformat() == "2026-06-06"
    assert delivery_note.due_date.isoformat() == "2026-06-07"
    assert delivery_note.business_fields["delivery_date"] == "2026-06-07"

    invoice = parser.parse((root / "06_tax_invoice.txt").read_text(), "06_tax_invoice.txt")
    assert invoice.issue_date.isoformat() == "2026-06-07"
    assert invoice.due_date.isoformat() == "2026-07-07"
    assert invoice.business_fields["payment_due_date"] == "2026-07-07"


def test_manufacturing_review_issues_and_item_matching_are_normalized():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "samples" / "manufacturing"
    parser = DocumentParser()
    service = DocumentWorkflowEnrichmentService()

    mismatch_text = (root / "07_amount_mismatch_needs_review.txt").read_text()
    mismatch = parser.parse(mismatch_text, "07_amount_mismatch_needs_review.txt")
    mismatch_document = Document(
        original_filename="07_amount_mismatch_needs_review.txt",
        stored_file_path="/tmp/07_amount_mismatch_needs_review.txt",
        mime_type="text/plain",
        document_type=mismatch.document_type,
        vendor_name=mismatch.vendor_name,
        customer_name=mismatch.customer_name,
        document_number=mismatch.document_number,
        issue_date=mismatch.issue_date,
        due_date=mismatch.due_date,
        extracted_amount=mismatch.extracted_amount,
        currency=mismatch.currency,
        line_items=mismatch.line_items,
        review_required=True,
    )
    mismatch_workflow = service.enrich(mismatch_document, mismatch_text)
    mismatch_issues = mismatch_workflow.workflow_metadata["normalized_review_issues"]
    assert [issue["code"] for issue in mismatch_issues] == ["amount_mismatch"]
    assert [issue["message_ko"] for issue in mismatch_issues].count("문서 합계금액과 품목 합계금액이 일치하지 않습니다.") == 1
    assert mismatch_workflow.warnings == ["문서 합계금액과 품목 합계금액이 일치하지 않습니다."]

    missing_quantity_text = (root / "08_missing_quantity_needs_review.txt").read_text()
    missing_quantity = parser.parse(missing_quantity_text, "08_missing_quantity_needs_review.txt")
    missing_quantity_document = Document(
        original_filename="08_missing_quantity_needs_review.txt",
        stored_file_path="/tmp/08_missing_quantity_needs_review.txt",
        mime_type="text/plain",
        document_type=missing_quantity.document_type,
        vendor_name=missing_quantity.vendor_name,
        customer_name=missing_quantity.customer_name,
        document_number=missing_quantity.document_number,
        issue_date=missing_quantity.issue_date,
        due_date=missing_quantity.due_date,
        extracted_amount=missing_quantity.extracted_amount,
        currency=missing_quantity.currency,
        line_items=missing_quantity.line_items,
        review_required=True,
    )
    missing_quantity_workflow = service.enrich(missing_quantity_document, missing_quantity_text)
    missing_quantity_issues = missing_quantity_workflow.workflow_metadata["normalized_review_issues"]
    assert [issue["message_ko"] for issue in missing_quantity_issues].count("1번째 품목의 수량이 비어 있습니다.") == 1
    assert missing_quantity_document.line_items[0].get("quantity") in (None, "")
    assert all("비어 있습니다" not in str(item.get("quantity") or "") for item in missing_quantity_document.line_items)

    ambiguous_text = (root / "09_item_matching_ambiguous.txt").read_text()
    ambiguous = parser.parse(ambiguous_text, "09_item_matching_ambiguous.txt")
    ambiguous_document = Document(
        original_filename="09_item_matching_ambiguous.txt",
        stored_file_path="/tmp/09_item_matching_ambiguous.txt",
        mime_type="text/plain",
        document_type=ambiguous.document_type,
        vendor_name=ambiguous.vendor_name,
        customer_name=ambiguous.customer_name,
        document_number=ambiguous.document_number,
        issue_date=ambiguous.issue_date,
        due_date=ambiguous.due_date,
        extracted_amount=ambiguous.extracted_amount,
        currency=ambiguous.currency,
        line_items=ItemMasterMatcher().match_line_items_against_masters(ambiguous.line_items, []),
        review_required=True,
    )
    ambiguous_workflow = service.enrich(ambiguous_document, ambiguous_text)
    ambiguous_issues = ambiguous_workflow.workflow_metadata["normalized_review_issues"]
    codes = [issue["code"] for issue in ambiguous_issues]
    messages = [issue["message_ko"] for issue in ambiguous_issues]
    assert messages.count("1번째 품목 품목코드 미확인") == 1
    assert messages.count("2번째 품목 품목코드 미확인") == 1
    assert messages.count("3번째 품목 품목코드 미확인") == 1
    assert messages.count("내부 품목 장부 매칭 필요") == 0
    assert messages.count("내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.") == 1
    assert "item_matching_skipped" in codes
    assert all(item.get("item_code") in (None, "") for item in ambiguous_document.line_items)
    assert all("품목코드 미확인" not in str(item.get("item_code") or "") for item in ambiguous_document.line_items)
    assert all(item.get("item_master_match_reason") == "NO_ITEM_MASTER" for item in ambiguous_document.line_items)
