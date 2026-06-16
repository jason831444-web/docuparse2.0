from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.monthly_report import MonthlyReportService


def _document(**overrides) -> Document:
    data = {
        "id": uuid4(),
        "original_filename": "doc.pdf",
        "stored_file_path": "/tmp/doc.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.purchase_order,
        "document_number": "PO-2026-0601",
        "vendor_name": "대한정밀",
        "customer_name": "한빛제조",
        "issue_date": date(2026, 6, 10),
        "created_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
        "currency": "KRW",
        "extracted_amount": Decimal("1000"),
        "processing_status": ProcessingStatus.confirmed,
        "line_items": [
            {
                "item_name": "S45C PIN",
                "specification": "8x60",
                "quantity": "10",
                "unit": "EA",
                "unit_price": "100",
                "supply_amount": "1000",
            }
        ],
    }
    data.update(overrides)
    return Document(**data)


def test_monthly_report_summary_party_and_item_aggregation():
    service = MonthlyReportService()
    documents = [
        _document(document_number="PO-1", customer_name="한빛제조", extracted_amount=Decimal("1000")),
        _document(
            document_number="PO-2",
            customer_name="한빛제조",
            extracted_amount=None,
            line_items=[
                {
                    "item_name": "S45C PIN",
                    "specification": "8x60",
                    "quantity": "5",
                    "unit_price": "100",
                    "supply_amount": "500",
                }
            ],
        ),
        _document(document_number="PO-3", processing_status=ProcessingStatus.needs_review, extracted_amount=Decimal("300")),
        _document(document_number="PO-MAY", issue_date=date(2026, 5, 31), extracted_amount=Decimal("9999")),
    ]

    report = service.build(documents, year=2026, month=6)

    assert report["summary"]["total_documents"] == 3
    assert report["summary"]["verified_documents"] == 2
    assert report["summary"]["pending_documents"] == 1
    assert report["summary"]["total_amount"] == 1500
    assert report["by_party"] == [{"name": "한빛제조", "document_count": 2, "total_amount": 1500}]
    assert report["by_item"][0]["item_name"] == "S45C PIN"
    assert report["by_item"][0]["quantity"] == 15
    assert report["by_item"][0]["total_amount"] == 1500


def test_monthly_report_issues_include_pending_missing_and_calculation_mismatch():
    service = MonthlyReportService()
    missing = _document(
        document_number="MISS-1",
        customer_name=None,
        vendor_name=None,
        line_items=[{"item_name": "", "quantity": None, "unit_price": None, "supply_amount": None}],
    )
    mismatch = _document(
        document_number="BAD-1",
        line_items=[{"item_name": "볼트", "quantity": "2", "unit_price": "100", "supply_amount": "250"}],
    )
    pending = _document(document_number="PEND-1", processing_status=ProcessingStatus.ready)

    report = service.build([missing, mismatch, pending], year=2026, month=6)

    assert report["issues"]["pending_documents"][0]["document_number"] == "PEND-1"
    assert report["issues"]["missing_required_fields"][0]["document_number"] == "MISS-1"
    assert "거래처명" in report["issues"]["missing_required_fields"][0]["description"]
    assert report["issues"]["calculation_mismatches"][0]["document_number"] == "BAD-1"
    assert report["summary"]["documents_with_errors"] == 3


def test_no_price_document_does_not_emit_amount_validation_issues():
    service = MonthlyReportService()
    document = _document(
        document_number="DN-1",
        document_type=DocumentType.delivery_note,
        extracted_amount=None,
        workflow_metadata={
            "taxonomy": {
                "document_profile": "no_price_document",
                "document_profiles": ["no_price_document", "inventory_movement_document"],
                "amount_required": False,
            }
        },
        line_items=[{"item_name": "SUS 와셔", "quantity": "1200", "unit": "EA"}],
    )

    report = service.build([document], year=2026, month=6)

    assert report["summary"]["verified_documents"] == 1
    assert report["summary"]["total_amount"] == 0
    assert report["issues"]["missing_required_fields"] == []
    assert report["issues"]["calculation_mismatches"] == []


def test_monthly_report_exports_xlsx_and_csv():
    pytest.importorskip("openpyxl")
    service = MonthlyReportService()
    report = service.build([_document()], year=2026, month=6)

    xlsx = service.to_excel(report)
    csv = service.to_csv(report)

    with ZipFile(BytesIO(xlsx)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")

    assert "Summary" in workbook_xml
    assert "By Party" in workbook_xml
    assert "By Item" in workbook_xml
    assert "Issues" in workbook_xml
    assert "Summary" in csv
    assert "By Party" in csv
    assert "S45C PIN" in csv
