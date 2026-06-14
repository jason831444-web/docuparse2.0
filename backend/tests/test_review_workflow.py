import json
from decimal import Decimal

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.export import document_to_json, documents_to_csv
from app.services.review_workflow import approve_document, update_issue_status, validate_approval


def _document(**kwargs) -> Document:
    data = {
        "original_filename": "doc.pdf",
        "stored_file_path": "/tmp/doc.pdf",
        "mime_type": "application/pdf",
        "document_type": DocumentType.invoice,
        "document_number": "INV-1",
        "vendor_name": "공급사",
        "customer_name": "고객사",
        "subtotal": Decimal("100"),
        "tax": Decimal("10"),
        "extracted_amount": Decimal("110"),
        "currency": "KRW",
        "processing_status": ProcessingStatus.needs_review,
        "review_required": True,
        "line_items": [{"item_name": "품목", "quantity": 1, "unit_price": 100, "supply_amount": 100, "tax_amount": 10, "line_total": 110}],
        "workflow_metadata": {
            "taxonomy": {
                "document_subtype": "tax_invoice",
                "document_profile": "tax_document",
                "document_profiles": ["tax_document", "priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "normalized_review_issues": [],
        },
    }
    data.update(kwargs)
    return Document(**data)


def test_approval_blocks_unresolved_critical_issue_until_resolved():
    document = _document(workflow_metadata={
        "taxonomy": {"document_profile": "priced_document", "document_profiles": ["priced_document"], "amount_required": True, "party_required": True},
        "normalized_review_issues": [{"code": "internal_item_ambiguous", "message_ko": "품목 후보 확인 필요", "field": "line_items.internal_item_code", "item_index": 0}],
    })

    blocked = approve_document(document)
    assert blocked.ok is False
    assert any("internal_item_ambiguous" in item for item in blocked.blocking)
    assert document.workflow_metadata["review"]["review_state"] == "blocked"

    update_issue_status(document, "internal_item_ambiguous:line_items.internal_item_code:0", "resolved", "Confirmed candidate")
    approved = approve_document(document, approval_note="확인 완료")

    assert approved.ok is True
    assert document.workflow_metadata["review"]["approved"] is True
    assert document.workflow_metadata["review"]["approval_note"] == "확인 완료"


def test_no_price_document_can_be_approved_without_total_or_currency():
    document = _document(
        document_type=DocumentType.general_document,
        extracted_amount=None,
        subtotal=None,
        tax=None,
        currency=None,
        vendor_name=None,
        customer_name=None,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "internal_transfer",
                "document_profile": "inventory_movement_document",
                "document_profiles": ["inventory_movement_document", "no_price_document"],
                "amount_required": False,
                "party_required": False,
            },
            "normalized_review_issues": [{"code": "missing_price_or_total", "message_ko": "금액 없음", "field": "extracted_amount"}],
        },
        line_items=[{"item_name": "베어링", "quantity": 10, "unit": "EA"}],
    )

    validation = validate_approval(document)

    assert validation.ok is True
    assert "missing_total" not in validation.warnings
    assert not any("missing_price_or_total" in item for item in validation.blocking)


def test_tax_document_amount_mismatch_blocks_approval():
    document = _document(extracted_amount=Decimal("111"))

    validation = approve_document(document)

    assert validation.ok is False
    assert "subtotal_tax_total_mismatch" in validation.blocking


def test_return_document_warns_about_amount_direction_and_related_document():
    document = _document(
        document_type=DocumentType.general_document,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "normalized_review_issues": [],
        },
    )

    validation = approve_document(document)

    assert validation.ok is True
    assert "amount_direction_requires_review" in validation.warnings
    assert "related_document_missing" in validation.warnings


def test_return_document_misclassified_as_delivery_note_blocks_approval():
    document = _document(
        document_type=DocumentType.delivery_note,
        workflow_metadata={
            "taxonomy": {
                "document_subtype": "return_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "normalized_review_issues": [],
        },
    )

    validation = validate_approval(document)

    assert validation.ok is False
    assert "return_document_misclassified_as_delivery_note" in validation.blocking


def test_vl_candidate_issue_warns_but_does_not_block_approval():
    document = _document(
        workflow_metadata={
            "taxonomy": {
                "document_profile": "priced_document",
                "document_profiles": ["priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "normalized_review_issues": [],
            "vl_candidates": [
                {
                    "provider": "paddleocr_vl_1_6_gguf",
                    "candidate_only": True,
                    "parser_integrated": False,
                    "issue_codes": ["vl_candidate_missing_document_total"],
                }
            ],
            "vl_candidate_summary": {
                "candidate_count": 1,
                "warning_count": 1,
                "issue_codes": ["vl_candidate_missing_document_total"],
                "provider_available_candidate": False,
            },
        },
    )

    validation = validate_approval(document)

    assert validation.ok is True
    assert "vl_candidate_review_required" in validation.warnings
    assert not any("vl_candidate" in item for item in validation.blocking)


def test_layout_debug_vl_candidate_issue_warns_but_does_not_block_approval():
    document = _document(
        workflow_metadata={
            "taxonomy": {
                "document_profile": "priced_document",
                "document_profiles": ["priced_document"],
                "amount_required": True,
                "party_required": True,
            },
            "normalized_review_issues": [],
            "layout_debug": {
                "vl_candidates": [
                    {
                        "provider": "paddleocr_vl_1_6_gguf",
                        "candidate_only": True,
                        "parser_integrated": False,
                        "issue_codes": ["manual_visual_check_not_performed"],
                    }
                ],
                "vl_candidate_summary": {
                    "candidate_count": 1,
                    "failure_count": 1,
                    "issue_codes": ["manual_visual_check_not_performed"],
                    "provider_available_candidate": False,
                },
            },
        },
    )

    validation = validate_approval(document)

    assert validation.ok is True
    assert "vl_candidate_review_required" in validation.warnings
    assert not any("vl_candidate" in item for item in validation.blocking)


def test_export_reflects_approval_metadata():
    document = _document()
    validation = approve_document(document, approval_note="ERP 입력 전 확인 완료")
    assert validation.ok is True

    payload = json.loads(document_to_json(document))
    csv = documents_to_csv([document])

    assert payload["export_policy"]["approved"] is True
    assert payload["export_policy"]["review_state"] == "approved"
    assert payload["export_policy"]["approval_note"] == "ERP 입력 전 확인 완료"
    assert "approved" in csv
    assert "ERP 입력 전 확인 완료" in csv
