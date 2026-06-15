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


def test_vl_candidate_gate_marks_clean_candidate_as_promotion_eligible_for_auto_promote():
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
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "full"
    assert result["reasons"] == ["validated_candidate_without_known_issues"]


def test_vl_candidate_gate_partially_promotes_blank_quantity_candidate_with_review():
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
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "partial"
    assert "vl_candidate_has_review_issues" in result["reasons"]


def test_vl_candidate_gate_partially_promotes_hidden_right_column_candidate_with_review():
    candidate = {
        "provider_available_candidate": True,
        "issue_codes": ["vl_candidate_remaining_quantity_hidden"],
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "DN-GEN-2026-003",
                "total": None,
            },
            "line_items": [
                {
                    "item_name": "베어링 하우징",
                    "ordered_quantity": 80,
                    "delivered_quantity": 50,
                }
            ],
            "line_item_count": 1,
            "issue_codes": ["vl_candidate_remaining_quantity_hidden"],
        },
    }

    document = _document()
    document.document_type = DocumentType.delivery_note
    document.document_number = "DN-GEN-2026-003"
    document.extracted_amount = None
    document.workflow_metadata = {"taxonomy": {"document_profiles": ["no_price_document"]}}

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "partial"
    assert "vl_candidate_remaining_quantity_hidden" in result["issue_codes"]


def test_vl_candidate_gate_partially_promotes_hidden_amount_or_arithmetic_mismatch_candidate_with_review():
    candidate = {
        "provider_available_candidate": True,
        "issue_codes": [
            "vl_candidate_explicit_quantity_price_amount_mismatch",
            "vl_candidate_row_amount_hidden_do_not_infer",
        ],
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "TS-GEN-2026-008",
                "total": None,
            },
            "line_items": [
                {
                    "item_name": "SUS304 3T PLATE",
                    "quantity": 3,
                    "unit_price": 35000,
                    "validation_warnings": [
                        "explicit_quantity_price_amount_mismatch",
                        "row_amount_hidden_do_not_infer",
                    ],
                }
            ],
            "line_item_count": 1,
            "issue_codes": [
                "vl_candidate_explicit_quantity_price_amount_mismatch",
                "vl_candidate_row_amount_hidden_do_not_infer",
            ],
        },
    }

    document = _document()
    document.document_type = DocumentType.transaction_statement
    document.document_number = "TS-GEN-2026-008"
    document.extracted_amount = None

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "partial"
    assert "vl_candidate_has_review_issues" in result["reasons"]


def test_vl_candidate_gate_keeps_raw_invalid_row_warning_as_review_candidate_only():
    candidate = {
        "provider_available_candidate": True,
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "PO-2026-0807-777",
                "total": "343200",
            },
            "line_items": [
                {
                    "item_name": "SUS304 PLATE",
                    "quantity": 1,
                    "validation_warnings": ["invalid_line_total"],
                }
            ],
            "line_item_count": 1,
            "issue_codes": ["vl_candidate_invalid_line_total"],
        },
    }

    document = _document()
    document.document_number = "PO-2026-0807-777"
    document.extracted_amount = Decimal("343200")

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is False
    assert result["promotion_mode"] == "none"
    assert "vl_candidate_has_review_issues" in result["reasons"]


def test_vl_candidate_gate_allows_repaired_malformed_amount_warning_for_partial_review():
    candidate = {
        "provider_available_candidate": True,
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "PO-2026-0807-777",
                "total": "343200",
            },
            "line_items": [
                {
                    "item_name": "SUS304 PLATE",
                    "quantity": 1,
                    "validation_warnings": ["malformed_amount_columns_repaired"],
                }
            ],
            "line_item_count": 1,
            "issue_codes": ["vl_candidate_malformed_amount_columns_repaired"],
        },
    }

    document = _document()
    document.document_number = "PO-2026-0807-777"
    document.extracted_amount = Decimal("343200")

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "partial"
    assert "vl_candidate_has_review_issues" in result["reasons"]


def test_vl_candidate_gate_partially_promotes_fax_row_boundary_warning():
    candidate = {
        "provider_available_candidate": True,
        "structured_candidate": {
            "candidate_only": True,
            "parser_integrated": False,
            "confirmed_promotion": False,
            "document": {
                "document_number": "FAX-PO-GEN-010",
                "total": "418000",
            },
            "line_items": [
                {
                    "item_name": "M8 볼트 / 와셔 SET",
                    "quantity": 1000,
                    "validation_warnings": ["fax_row_boundary_uncertain"],
                }
            ],
            "line_item_count": 1,
            "issue_codes": ["vl_candidate_fax_row_boundary_uncertain"],
        },
    }

    document = _document()
    document.document_number = "FAX-PO-GEN-010"
    document.extracted_amount = Decimal("418000")

    result = VLCandidateValidationGate().evaluate(document, candidate)

    assert result["decision"] == "review_required"
    assert result["auto_promote"] is True
    assert result["promotion_mode"] == "partial"
    assert "vl_candidate_fax_row_boundary_uncertain" in result["issue_codes"]


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
    assert result["promotion_mode"] == "none"
