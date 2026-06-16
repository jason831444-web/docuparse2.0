import csv
import json
from datetime import date
from decimal import Decimal
from io import StringIO

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.export import document_to_json, documents_to_csv
from app.services.monthly_report import MonthlyReportService
from app.services.review_workflow import approve_document


def _workflow_document(**overrides) -> Document:
    data = {
        "original_filename": "workflow-visible-crop.pdf",
        "stored_file_path": "/tmp/workflow-visible-crop.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.purchase_order,
        "document_number": "PO-WF-2026-001",
        "vendor_name": "대한정밀부품",
        "customer_name": "한빛제조",
        "issue_date": date(2026, 6, 16),
        "due_date": date(2026, 6, 25),
        "currency": "KRW",
        "subtotal": Decimal("150000"),
        "tax": None,
        "extracted_amount": Decimal("150000"),
        "processing_status": ProcessingStatus.ready,
        "review_required": True,
        "line_items": [
            {
                "item_name": "SUS304 PLATE",
                "document_item_code": "PLT-SUS304",
                "specification": "2T 1000x2000",
                "quantity": "3",
                "unit": "EA",
                "unit_price": "50000",
                "supply_amount": "150000",
                "validation_warnings": ["row_amount_hidden_do_not_infer"],
            }
        ],
        "workflow_metadata": {
            "taxonomy": {
                "document_subtype": "purchase_order",
                "document_profile": "priced_document",
                "document_profiles": ["priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "document_quality": {
                "page_count": 1,
                "overall_quality_score": 0.72,
                "possible_right_column_crop": True,
                "visible_columns": [
                    "item_name",
                    "document_item_code",
                    "specification",
                    "quantity",
                    "unit",
                    "unit_price",
                    "supply_amount",
                ],
                "hidden_or_cropped_columns": ["tax_amount", "line_total"],
                "review_reasons": ["visual_crop_or_truncated_column"],
                "pages": [
                    {
                        "page_index": 0,
                        "width": 1400,
                        "height": 1000,
                        "quality_score": 0.72,
                        "possible_right_column_crop": True,
                        "visible_columns": ["item_name", "quantity", "unit_price", "supply_amount"],
                        "hidden_or_cropped_columns": ["tax_amount", "line_total"],
                    }
                ],
            },
            "field_provenance": {
                "line_items.0.supply_amount": {
                    "source": "visual_source",
                    "provider": "paddleocr_vl_1_6_gguf",
                    "page": 0,
                    "visible": True,
                    "review_required": False,
                },
                "line_items.0.tax_amount": {
                    "source": "hidden_or_cropped_column",
                    "provider": "document_quality",
                    "page": 0,
                    "visible": False,
                    "review_required": True,
                    "reason": "visual_crop_or_truncated_column",
                },
                "line_items.0.line_total": {
                    "source": "hidden_or_cropped_column",
                    "provider": "document_quality",
                    "page": 0,
                    "visible": False,
                    "review_required": True,
                    "reason": "visual_crop_or_truncated_column",
                },
            },
            "normalized_review_issues": [
                {
                    "code": "visual_crop_or_truncated_column",
                    "severity": "warning",
                    "field": "line_items.amount_columns",
                    "message_ko": "오른쪽 금액 컬럼이 잘렸을 수 있어 세액/합계는 검토 후보로만 남깁니다.",
                }
            ],
        },
    }
    data.update(overrides)
    return Document(**data)


def test_quality_provenance_review_export_and_report_workflow_stays_consistent():
    document = _workflow_document()

    approval = approve_document(document, approval_note="보이는 공급가액만 업무데이터로 사용")
    assert approval.ok is True
    assert "vl_candidate_review_required" not in approval.blocking

    json_payload = json.loads(document_to_json(document))
    csv_rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))
    report = MonthlyReportService().build([document], year=2026, month=6)

    assert json_payload["workflow_metadata"]["document_quality"]["possible_right_column_crop"] is True
    assert json_payload["workflow_metadata"]["field_provenance"]["line_items.0.tax_amount"]["visible"] is False
    assert json_payload["workflow_metadata"]["field_provenance"]["line_items.0.tax_amount"]["review_required"] is True
    assert json_payload["canonical_export"]["line_items"][0]["supply_amount"] == "150000"
    assert json_payload["canonical_export"]["line_items"][0]["tax_amount"] in (None, "")
    assert json_payload["canonical_export"]["line_items"][0]["line_total"] in (None, "")

    assert csv_rows[0]["공급가액"] == "150000"
    assert csv_rows[0]["세액"] == ""
    assert csv_rows[0]["합계금액"] == ""
    assert csv_rows[0]["document_total"] == "150000"
    assert csv_rows[0]["approved"] == "True"
    assert "visual_crop_or_truncated_column" in csv_rows[0]["review_reasons"]

    assert report["summary"]["total_documents"] == 1
    assert report["summary"]["verified_documents"] == 1
    assert report["summary"]["total_amount"] == 150000
    assert report["by_party"][0]["name"] == "한빛제조"
    assert report["by_item"][0]["item_name"] == "SUS304 PLATE"
    assert report["by_item"][0]["total_amount"] == 150000


def test_no_price_quality_warning_does_not_create_amount_export_or_report_issue():
    document = _workflow_document(
        document_type=DocumentType.delivery_note,
        document_number="DN-WF-2026-002",
        vendor_name="대영부품",
        customer_name="오성테크",
        subtotal=None,
        tax=None,
        extracted_amount=None,
        currency=None,
        processing_status=ProcessingStatus.confirmed,
        review_required=True,
        line_items=[
            {
                "item_name": "육각볼트",
                "specification": "M8x20",
                "quantity": "1000",
                "unit": "EA",
                "validation_warnings": ["no_price_document_amount_blocker"],
            }
        ],
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "delivery_note",
                "document_profile": "no_price_document",
                "document_profiles": ["no_price_document", "inventory_movement_document"],
                "amount_required": False,
                "party_required": True,
            },
            "document_quality": {
                "overall_quality_score": 0.68,
                "possible_right_column_crop": True,
                "visible_columns": ["item_name", "specification", "quantity", "unit"],
                "hidden_or_cropped_columns": ["tax_amount", "line_total", "remarks"],
                "review_reasons": ["visual_crop_or_truncated_column"],
            },
            "field_provenance": {
                "line_items.0.quantity": {"source": "visual_source", "visible": True, "review_required": False},
                "line_items.0.line_total": {"source": "no_price_policy", "visible": False, "review_required": False},
            },
            "normalized_review_issues": [
                {
                    "code": "visual_crop_or_truncated_column",
                    "severity": "warning",
                    "field": "line_items.remarks",
                    "message_ko": "오른쪽 비고 컬럼이 잘렸을 수 있습니다.",
                }
            ],
        },
    )

    csv_rows = list(csv.DictReader(StringIO(documents_to_csv([document]))))
    report = MonthlyReportService().build([document], year=2026, month=6)
    payload = json.loads(document_to_json(document))

    assert payload["canonical_export"]["policy"]["amount_required"] is False
    assert payload["canonical_export"]["document"]["currency"] is None
    assert payload["canonical_export"]["document"]["total"] == ""
    assert payload["canonical_export"]["line_items"][0]["quantity"] == "1000"
    assert payload["canonical_export"]["line_items"][0]["tax_amount"] in (None, "")
    assert payload["canonical_export"]["line_items"][0]["line_total"] in (None, "")

    assert csv_rows[0]["수량"] == "1000"
    assert csv_rows[0]["공급가액"] == ""
    assert csv_rows[0]["세액"] == ""
    assert csv_rows[0]["합계금액"] == ""
    assert csv_rows[0]["document_total"] == ""

    assert report["summary"]["verified_documents"] == 1
    assert report["summary"]["total_amount"] == 0
    assert report["issues"]["calculation_mismatches"] == []
    assert report["issues"]["missing_required_fields"] == []
