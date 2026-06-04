from datetime import date
from decimal import Decimal

from app.models.document import DocumentType
from app.services.ai_document_understanding import AIDocumentUnderstandingResult
from app.services.ai_merge import AIResultMerger
from app.services.parser import ParsedDocument


def test_ai_merge_fills_missing_customer_without_overwriting_valid_parser_total():
    parsed = ParsedDocument(
        document_type=DocumentType.invoice,
        vendor_name="성진전자",
        customer_name=None,
        document_number="INV-1",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        extracted_amount=Decimal("543400"),
        line_items=[{"item_name": "PCB Connector 12P", "item_code": "CON-PCB-12P", "quantity": 1500, "unit_price": 300, "line_total": 495000}],
        category="invoice",
    )
    ai = AIDocumentUnderstandingResult(
        document_type=DocumentType.quotation,
        customer_name="네오팩토리",
        extracted_amount=Decimal("999999"),
        line_items=[],
    )

    merged = AIResultMerger().merge(parsed, ai)

    assert merged.result.document_type == DocumentType.invoice
    assert merged.result.customer_name == "네오팩토리"
    assert merged.result.extracted_amount == Decimal("543400")
    assert any(issue["code"] == "amount_conflict" for issue in merged.review_issues)


def test_ai_merge_does_not_add_vendor_sku_as_duplicate_line_item():
    parsed = ParsedDocument(
        document_type=DocumentType.invoice,
        line_items=[{"item_name": "PCB Connector 12P", "item_code": "CON-PCB-12P", "quantity": 1500, "line_total": 495000}],
        category="invoice",
    )
    ai = AIDocumentUnderstandingResult(
        document_type=DocumentType.invoice,
        line_items=[
            {"item_name": "PCB Connector 12P", "item_code": "CON-PCB-12P", "quantity": 1500, "line_total": 495000},
            {"item_name": "CON-PCB-12P", "quantity": "확인 필요"},
        ],
    )

    merged = AIResultMerger().merge(parsed, ai)

    assert len(merged.result.line_items) == 1
    assert merged.result.line_items[0]["quantity"] == Decimal("1500")
    assert any(issue["code"] == "duplicate_sku_as_item_name" for issue in merged.review_issues)


def test_ai_merge_sanitizes_warning_text_from_structured_fields():
    parsed = ParsedDocument(
        document_type=DocumentType.quotation,
        line_items=[{"item_name": "고정 플레이트", "item_code": "PLT-FIX-02", "quantity": None, "line_total": 308000}],
        category="quotation",
    )
    ai = AIDocumentUnderstandingResult(
        document_type=DocumentType.quotation,
        line_items=[{"item_name": "고정 플레이트", "item_code": "품목코드 미확인", "quantity": "확인 필요", "line_total": "308000"}],
    )

    merged = AIResultMerger().merge(parsed, ai)

    assert merged.result.line_items[0]["item_code"] == "PLT-FIX-02"
    assert merged.result.line_items[0]["quantity"] is None
    assert merged.result.line_items[0]["line_total"] == Decimal("308000")
