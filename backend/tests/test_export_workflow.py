import json
from decimal import Decimal
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.export import document_to_json, documents_to_csv, documents_to_excel, tax_invoice_to_draft_xml


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


def test_csv_export_appends_taxonomy_policy_columns_without_breaking_existing_columns():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "tax_invoice",
            "document_profile": "tax_document",
            "document_profiles": ["tax_document", "priced_document"],
            "layout_profile": "text_layer_pdf",
            "amount_required": True,
            "party_required": True,
            "evidence": ["세금계산서"],
        },
        "normalized_review_issues": [{"code": "internal_item_ambiguous", "message_ko": "품목 후보 확인 필요", "item_index": 0}],
    }

    csv = documents_to_csv([document])

    assert "문서유형,공급업체,고객사" in csv
    assert "document_subtype" in csv
    assert "document_profile" in csv
    assert "document_profiles" in csv
    assert "layout_profile" in csv
    assert "amount_required" in csv
    assert "export_policy" in csv
    assert "line_review_flags" in csv
    assert "tax_invoice" in csv
    assert "tax_document" in csv
    assert "internal_item_ambiguous" in csv


def test_json_export_includes_canonical_taxonomy_and_policy():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {
        "taxonomy": {
            "document_subtype": "tax_invoice",
            "document_profile": "tax_document",
            "document_profiles": ["tax_document", "priced_document"],
            "amount_required": True,
            "party_required": True,
        }
    }

    payload = json.loads(document_to_json(document))

    assert payload["document_taxonomy"]["document_subtype"] == "tax_invoice"
    assert payload["export_policy"]["export_policy"] == "tax_document_consistency"
    assert payload["canonical_export"]["document"]["document_subtype"] == "tax_invoice"
    assert payload["canonical_export"]["policy"]["amount_required"] is True
    assert payload["canonical_export"]["line_items"][0]["line_index"] == 1


def test_internal_transfer_export_treats_missing_amounts_as_policy_not_fake_total():
    document = Document(
        original_filename="TRF.pdf",
        stored_file_path="/tmp/TRF.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="TRF-2026-0922-002",
        processing_status=ProcessingStatus.needs_review,
        review_required=True,
        currency=None,
        extracted_amount=None,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "internal_transfer",
                "document_profile": "inventory_movement_document",
                "document_profiles": ["inventory_movement_document", "no_price_document"],
                "amount_required": False,
                "party_required": False,
            }
        },
        line_items=[{"item_name": "베어링 하우징", "quantity": 12, "unit": "EA"}],
    )

    csv = documents_to_csv([document])
    payload = json.loads(document_to_json(document))

    assert payload["canonical_export"]["policy"]["amount_required"] is False
    assert payload["canonical_export"]["policy"]["party_required"] is False
    assert payload["canonical_export"]["document"]["currency"] is None
    assert payload["canonical_export"]["document"]["total"] == ""
    assert "inventory_movement_no_price" in csv
    assert "amount_not_required" in csv
    assert "TRF-2026-0922-002" in csv


def test_credit_note_export_preserves_review_warning_and_related_document():
    document = Document(
        original_filename="RTN.pdf",
        stored_file_path="/tmp/RTN.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.general_document,
        document_number="RTN-2026-0919-011",
        vendor_name="대영부품",
        customer_name="오성테크",
        extracted_amount=Decimal("12100"),
        currency="KRW",
        processing_status=ProcessingStatus.needs_review,
        review_required=True,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "business_fields": {"related_document_number": "DN-2026-0914-2F"},
        },
        line_items=[{"item_name": "반품품목", "line_total": Decimal("12100")}],
    )

    payload = json.loads(document_to_json(document))
    csv = documents_to_csv([document])

    assert payload["canonical_export"]["document"]["document_subtype"] == "credit_note"
    assert payload["canonical_export"]["policy"]["related_document_number"] == "DN-2026-0914-2F"
    assert "amount_direction_requires_review" in payload["canonical_export"]["policy"]["export_warning"]
    assert "return_or_credit_review" in csv


def test_excel_export_contains_taxonomy_columns_in_combined_sheet():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = {"taxonomy": {"document_subtype": "commercial_invoice", "document_profile": "foreign_currency_document", "document_profiles": ["foreign_currency_document"]}}

    with ZipFile(BytesIO(documents_to_excel([document], sheet_mode="combined"))) as archive:
        xml_payload = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".xml"))

    assert "document_subtype" in xml_payload
    assert "document_profile" in xml_payload
    assert "commercial_invoice" in xml_payload


def test_export_without_taxonomy_metadata_still_works():
    document = _document("INV-1", "네오팩토리", Decimal("100"))
    document.workflow_metadata = None
    document.ingestion_metadata = None

    csv = documents_to_csv([document])
    payload = json.loads(document_to_json(document))

    assert "INV-1" in csv
    assert "document_subtype" in csv
    assert payload["canonical_export"]["document"]["document_type"] == "invoice"
    assert payload["canonical_export"]["policy"]["export_policy"] in {"priced_document", "tax_document_consistency"}
