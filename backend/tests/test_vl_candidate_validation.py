from decimal import Decimal

from app.models.document import Document, DocumentType
from app.services.vl_candidate_validation import VLCandidateValidationGate


def _document() -> Document:
    return Document(
        original_filename="QT-2026-0808-009.pdf",
        stored_file_path="/tmp/QT-2026-0808-009.pdf",
        mime_type="application/pdf",
        document_type=DocumentType.quotation,
        document_number="QT-2026-0808-009",
        extracted_amount=Decimal("473000"),
        currency="KRW",
        workflow_metadata={
            "taxonomy": {
                "document_profile": "priced_document",
                "document_profiles": ["priced_document"],
            }
        },
    )


def test_vl_candidate_gate_marks_clean_candidate_as_promotion_eligible_but_not_auto_promoted():
    candidate = {
        "provider_available_candidate": True,
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "QT-2026-0808-009",
                "total": "473000",
                "currency": "KRW",
            },
            "line_items": [{"item_name": "스테인리스 브라켓", "quantity": 100}],
            "line_item_count": 1,
            "issue_codes": [],
        },
    }

    result = VLCandidateValidationGate().evaluate(_document(), candidate)

    assert result["decision"] == "promotion_eligible"
    assert result["auto_promote"] is False
    assert result["reasons"] == ["validated_candidate_without_known_issues"]


def test_vl_candidate_gate_keeps_blank_quantity_candidate_in_review():
    candidate = {
        "provider_available_candidate": True,
        "issue_codes": ["vl_candidate_missing_quantity"],
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "QT-2026-0808-009",
                "total": "473000",
            },
            "line_items": [{"item_name": "고정 플레이트", "validation_warnings": ["missing_quantity"]}],
            "line_item_count": 1,
            "issue_codes": ["vl_candidate_missing_quantity"],
        },
    }

    result = VLCandidateValidationGate().evaluate(_document(), candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is False
    assert "vl_candidate_has_review_issues" in result["reasons"]


def test_vl_candidate_gate_rejects_no_price_amount_conflict():
    document = _document()
    document.document_type = DocumentType.delivery_note
    document.extracted_amount = None
    document.currency = None
    document.workflow_metadata = {
        "taxonomy": {
            "document_profile": "no_price_document",
            "document_profiles": ["no_price_document", "inventory_movement_document"],
        }
    }
    candidate = {
        "provider_available_candidate": True,
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {"document_number": "DN-1", "total": "1000", "currency": "KRW"},
            "line_items": [{"item_name": "품목", "line_total": "1000"}],
            "line_item_count": 1,
            "issue_codes": [],
        },
    }

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "reject"
    assert "no_price_candidate_amount_conflict" in result["issue_codes"]
    assert result["auto_promote"] is False
