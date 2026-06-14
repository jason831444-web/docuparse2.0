from app.scripts.e2e_regression_report import _compare_result, _markdown_report


def test_e2e_report_preserves_review_reason_summary_without_changing_pass_status():
    row = _compare_result(
        {
            "filename": "22_photo_internal_branch_transfer_delivery.pdf",
            "document_type": "general_document",
            "document_subtype": "internal_transfer",
            "document_profile": "inventory_movement_document",
            "document_profiles": ["inventory_movement_document", "no_price_document"],
            "document_number": "TRF-2026-0922-002",
            "extracted_amount": None,
            "currency": None,
            "line_items_count": 3,
            "processing_status": "needs_review",
            "review_required": True,
            "review_reasons": ["missing_quantity", "missing_quantity", "missing_quantity"],
            "vl_candidate_count": 1,
            "vl_candidate_issue_codes": ["vl_candidate_missing_document_total"],
        },
        {
            "document_type": "internal_transfer",
            "document_number": "TRF-2026-0922-002",
            "line_items": 3,
        },
        source="/tmp/example.log",
    )

    assert row["status"] == "WARN"
    assert row["review_reason_summary"] == "missing_quantity x3"
    assert row["processing_status"] == "needs_review"
    assert row["review_required"] is True

    markdown = _markdown_report([row])
    assert "## Operational Summary" in markdown
    assert "PASS/WARN/FAIL: 0 / 1 / 0" in markdown
    assert "Review Required: 1 (100.0%)" in markdown
    assert "Processing Statuses: needs_review x1" in markdown
    assert "Top Review Signals: missing_quantity x3" in markdown
    assert "| Status | Processing | Review Required |" in markdown
    assert "BBox Candidates | VL Candidates | VL Issues" in markdown
    assert "vl_candidate_missing_document_total" in markdown
    assert "non-blocking informational codes" in markdown
    assert "missing_quantity x3" in markdown
    assert "needs_review" in markdown


def test_e2e_report_explains_low_recall_when_review_candidates_are_preserved():
    row = _compare_result(
        {
            "filename": "15_photo_tax_invoice_rounding_adjustment.pdf",
            "document_type": "invoice",
            "document_subtype": "tax_invoice",
            "document_profile": "tax_document",
            "document_profiles": ["tax_document", "priced_document"],
            "document_number": "INV-2026-0915-ROUND",
            "currency": "KRW",
            "extracted_amount": "296680.00",
            "line_items_count": 1,
            "processing_status": "needs_review",
            "review_required": True,
            "review_reasons": ["missing_quantity", "missing_document_item_code"],
            "review_candidates_count": 2,
            "vl_candidate_count": 0,
        },
        {
            "document_type": "tax_invoice",
            "document_number": "INV-2026-0915-ROUND",
            "currency": "KRW",
            "total_amount": 296680,
            "line_items": 3,
        },
        source="/tmp/photo.log",
    )

    assert row["status"] == "WARN"
    assert "line_items_count: expected at least 3, got 1; review candidates present: 2" in row["reasons"]
    assert row["review_candidates_count"] == 2
