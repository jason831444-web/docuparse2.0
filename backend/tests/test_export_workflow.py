from decimal import Decimal
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.export import documents_to_csv, documents_to_excel, tax_invoice_to_draft_xml


def _document(number: str, customer: str, amount: Decimal) -> Document:
    return Document(
        original_filename=f"{number}.pdf",
        stored_file_path=f"/tmp/{number}.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.invoice,
        document_number=number,
        vendor_name="공급사",
        customer_name=customer,
        issue_date=date(2026, 8, 3),
        subtotal=amount,
        tax=Decimal("0"),
        extracted_amount=amount,
        currency="KRW",
        processing_status=ProcessingStatus.ready,
        line_items=[{"item_name": "품목", "quantity": 1, "unit_price": amount, "supply_amount": amount, "tax_amount": 0, "line_total": amount}],
    )


def test_export_uses_only_documents_passed_by_caller():
    selected = [_document("INV-1", "네오팩토리", Decimal("100"))]
    csv = documents_to_csv(selected)

    assert "INV-1" in csv
    assert "INV-2" not in csv


def test_excel_export_can_split_by_party_tabs():
    docs = [_document("INV-1", "네오팩토리", Decimal("100")), _document("INV-2", "오성테크", Decimal("200"))]
    with ZipFile(BytesIO(documents_to_excel(docs, sheet_mode="party_tabs"))) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")

    assert "네오팩토리" in workbook_xml
    assert "오성테크" in workbook_xml


def test_tax_invoice_xml_draft_validates_amounts_before_export():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    xml = tax_invoice_to_draft_xml(document).decode("utf-8")

    assert "TaxInvoiceDraft" in xml
    assert "INV-1" in xml

    document.extracted_amount = Decimal("90")
    with pytest.raises(ValueError):
        tax_invoice_to_draft_xml(document)
