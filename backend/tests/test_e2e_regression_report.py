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
            "line_items": [
                {"quantity": None, "validation_warnings": ["inspection_quantity_breakdown_missing"]},
                {"quantity": None, "validation_warnings": ["inspection_quantity_breakdown_missing"]},
                {"quantity": None, "validation_warnings": []},
            ],
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
    assert row["row_signal_summary"] == (
        "inspection_quantity_breakdown_missing x2, row_missing_quantity x3, vl_candidate_missing_document_total"
    )
    assert row["row_signal_count"] == 6
    assert row["processing_status"] == "needs_review"
    assert row["review_required"] is True

    markdown = _markdown_report([row])
    assert "## Operational Summary" in markdown
    assert "PASS/WARN/FAIL: 0 / 1 / 0" in markdown
    assert "Review Required: 1 (100.0%)" in markdown
    assert "Processing Statuses: needs_review x1" in markdown
    assert "Top Review Signals: missing_quantity x3" in markdown
    assert "Top Row-Level Signals: row_missing_quantity x3, inspection_quantity_breakdown_missing x2, vl_candidate_missing_document_total x1" in markdown
    assert "| Status | Processing | Review Required |" in markdown
    assert "Row Signals | Provider | Fallback | BBox Candidates | VL Candidates | VL Issues" in markdown
    assert "vl_candidate_missing_document_total" in markdown
    assert "inspection_quantity_breakdown_missing x2, row_missing_quantity x3" in markdown
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


def test_e2e_report_surfaces_row_level_warning_summary_without_forcing_failure():
    row = _compare_result(
        {
            "filename": "21_photo_fax_po_misaligned_amounts.pdf",
            "document_type": "purchase_order",
            "document_number": "FAX-PO-2026-0921",
            "currency": "KRW",
            "extracted_amount": "418000.00",
            "line_items_count": 2,
            "line_items": [
                {
                    "item_name": "베어링 하우징",
                    "quantity": None,
                    "line_total": 176000,
                    "validation_warnings": ["fax_row_boundary_uncertain", "untrusted_ocr_amount"],
                },
                {
                    "item_name": "S45C PIN 8X60",
                    "quantity": None,
                    "line_total": 66000,
                    "validation_warnings": ["fax_row_boundary_uncertain", "untrusted_ocr_amount"],
                },
            ],
            "processing_status": "needs_review",
            "review_required": True,
            "review_reasons": ["bbox_table_candidate_uncertain"],
            "review_candidates_count": 1,
        },
        {
            "document_type": "purchase_order",
            "document_number": "FAX-PO-2026-0921",
            "currency": "KRW",
            "total_amount": 418000,
            "line_items": 2,
        },
        source="/tmp/photo.log",
    )

    assert row["status"] == "PASS"
    assert row["row_signal_summary"] == (
        "fax_row_boundary_uncertain x2, row_missing_quantity x2, untrusted_ocr_amount x2"
    )
    markdown = _markdown_report([row])
    assert "Top Row-Level Signals: fax_row_boundary_uncertain x2" in markdown
    assert "untrusted_ocr_amount x2" in markdown
