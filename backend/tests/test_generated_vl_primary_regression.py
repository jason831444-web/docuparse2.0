from __future__ import annotations

import json

from app.scripts.run_generated_vl_primary_regression import compare_expected_actual
from app.scripts import run_generated_vl_primary_regression as regression


def test_compare_rejects_hidden_row_amount_confirmation():
    expected = {
        "visual_crop": True,
        "visible_columns": ["item_name", "document_item_code", "quantity", "unit_price", "supply_amount"],
        "hidden_or_cropped_columns": ["tax_amount", "line_total"],
        "line_items": [
            {
                "item_name": "고정 플레이트",
                "document_item_code": "PLT-FIX-02",
                "quantity": None,
                "expected_review_flags": ["missing_quantity", "row_amount_hidden_do_not_infer"],
            }
        ],
    }
    actual = {
        "line_items": [
            {
                "item_name": "고정 플레이트",
                "document_item_code": "PLT-FIX-02",
                "quantity": None,
                "unit_price": 2800,
                "supply_amount": 280000,
                "tax_amount": 28000,
                "line_total": 308000,
            }
        ]
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "FAIL"
    assert {issue["code"] for issue in result["failures"]} == {"row_amount_hidden_do_not_infer"}


def test_compare_rejects_blank_quantity_hallucination():
    expected = {
        "visible_columns": ["item_name", "document_item_code", "quantity"],
        "hidden_or_cropped_columns": [],
        "line_items": [
            {
                "item_name": "고정 플레이트",
                "document_item_code": "PLT-FIX-02",
                "quantity": None,
                "expected_review_flags": ["missing_quantity"],
            }
        ],
    }
    actual = {
        "line_items": [
            {
                "item_name": "고정 플레이트",
                "document_item_code": "PLT-FIX-02",
                "quantity": 100,
            }
        ]
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "FAIL"
    assert result["failures"][0]["code"] == "blank_quantity_preservation_failed"


def test_compare_does_not_treat_delivered_quantity_as_blank_quantity_hallucination():
    expected = {
        "no_price_document": True,
        "visible_columns": ["item_name", "document_item_code", "ordered_quantity", "delivered_quantity"],
        "line_items": [
            {
                "item_name": "베어링 하우징",
                "document_item_code": "BRG-H-100",
                "quantity": None,
                "ordered_quantity": 80,
                "delivered_quantity": 50,
            }
        ],
    }
    actual = {
        "line_items": [
            {
                "item_name": "베어링 하우징",
                "document_item_code": "BRG-H-100",
                "quantity": 50,
                "ordered_quantity": 80,
                "delivered_quantity": 50,
            }
        ]
    }

    result = compare_expected_actual(expected, actual, {})

    assert "blank_quantity_preservation_failed" not in {issue["code"] for issue in result["failures"]}


def test_compare_rejects_visible_numeric_field_mismatch():
    expected = {
        "visible_columns": ["item_name", "document_item_code", "quantity", "supply_amount"],
        "line_items": [
            {
                "item_name": "SUS304 3T PLATE",
                "document_item_code": "PLT-3T",
                "quantity": 3,
                "supply_amount": 105000,
            }
        ],
    }
    actual = {
        "line_items": [
            {
                "item_name": "SUS304 3T PLATE",
                "document_item_code": "PLT-3T",
                "quantity": 3,
                "supply_amount": 10,
            }
        ]
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "FAIL"
    assert result["failures"][0]["code"] == "visible_field_mismatch"
    assert result["failures"][0]["field"] == "supply_amount"


def test_compare_rejects_no_price_amounts_but_allows_review():
    expected = {
        "no_price_document": True,
        "visible_columns": ["item_name", "document_item_code", "quantity", "unit"],
        "hidden_or_cropped_columns": [],
        "line_items": [
            {
                "item_name": "S45C PIN",
                "document_item_code": "PIN-8X60",
                "quantity": 300,
                "unit": "EA",
            }
        ],
    }
    actual = {
        "currency": "KRW",
        "extracted_amount": 180000,
        "review_required": True,
        "line_items": [
            {
                "item_name": "S45C PIN",
                "document_item_code": "PIN-8X60",
                "quantity": 300,
                "unit": "EA",
                "supply_amount": 180000,
            }
        ],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "FAIL"
    assert "no_price_document_amount_blocker" in {issue["code"] for issue in result["failures"]}
    assert "no_price_line_amount_created" in {issue["code"] for issue in result["failures"]}


def test_compare_rejects_exchange_rate_as_total_or_amount():
    expected = {
        "document_type": "invoice",
        "currency": "USD",
        "total_amount": 650,
        "visible_columns": ["item_name", "quantity", "unit_price"],
        "line_items": [{"item_name": "Linear Guide Rail HGW20", "quantity": 10, "unit_price": 45}],
    }
    actual = {
        "document_type": "invoice",
        "currency": "USD",
        "extracted_amount": 1370,
        "line_items": [{"item_name": "Linear Guide Rail HGW20", "quantity": 10, "unit_price": 1370}],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "FAIL"
    assert "exchange_rate_not_total" in {issue["code"] for issue in result["failures"]}


def test_compare_warns_when_visible_field_missing_without_dangerous_contamination():
    expected = {
        "visual_crop": True,
        "visible_columns": ["item_name", "document_item_code", "quantity"],
        "hidden_or_cropped_columns": ["line_total"],
        "line_items": [
            {
                "item_name": "M8 육각 볼트",
                "document_item_code": "BOLT-M8-20",
                "quantity": 1500,
                "expected_review_flags": ["row_amount_hidden_do_not_infer"],
            }
        ],
    }
    actual = {
        "workflow_metadata": {
            "normalized_review_issues": [{"code": "row_amount_hidden_do_not_infer"}],
        },
        "line_items": [
            {
                "item_name": "M8 육각 볼트",
                "document_item_code": "BOLT-M8-20",
            }
        ],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "WARN"
    assert not result["failures"]
    assert "visible_field_missing" in {issue["code"] for issue in result["warnings"]}


def test_dangerous_contamination_ignores_metadata_only_failures():
    failures = [
        {
            "code": "document_number_missing",
            "field_sources": {"tax": "heuristic_fallback"},
            "message": "Document number is missing but no amount was confirmed.",
        },
        {
            "code": "visible_field_missing",
            "field": "quantity",
            "actual_value": None,
        },
    ]

    assert regression.dangerous_contamination_failures(failures) == []


def test_dangerous_contamination_counts_confirmed_no_price_amounts():
    failures = [
        {
            "code": "no_price_line_amount_created",
            "line_index": 1,
            "actual_value": {"supply_amount": 120000},
        }
    ]

    assert regression.dangerous_contamination_failures(failures) == failures


def test_expected_metadata_row_uses_aliases_for_report_display():
    expected = regression._expected_from_metadata_row(
        {
            "filename": "receipt.jpg",
            "document_type": "receipt",
            "receipt_no": "RC-2026-0001",
            "merchant_name": "가온마트",
            "transaction_date": "2026.06.12",
            "total": 75500,
            "line_items": 3,
        }
    )

    assert expected["document_number"] == "RC-2026-0001"
    assert expected["vendor"] == "가온마트"
    assert expected["issue_date"] == "2026.06.12"
    assert expected["total_amount"] == 75500
    assert regression.summarize_expected(expected)["line_item_count"] == 3


def test_compare_separates_fixture_label_mismatch_from_type_mismatch():
    expected = {"document_type": "quotation"}
    actual = {
        "document_type": "purchase_order",
        "raw_text": "발주서\n문서번호 PO-2026-0001\n공급업체 한빛정밀",
        "workflow_metadata": {"taxonomy": {"document_profile": "purchase_order"}},
    }

    result = compare_expected_actual(expected, actual, {})

    codes = {issue["code"] for issue in result["warnings"]}
    assert "fixture_label_mismatch" in codes
    assert "document_type_mismatch" not in codes


def test_compare_separates_fixture_document_number_from_business_document_number():
    expected = {"document_type": "invoice", "document_number": "DOC-002"}
    actual = {"document_type": "invoice", "document_number": "INV-2026-0002", "line_items": []}

    result = compare_expected_actual(expected, actual, {})

    codes = {issue["code"] for issue in result["warnings"]}
    assert "fixture_label_mismatch" in codes
    assert "document_number_mismatch" not in codes


def test_compare_accepts_taxonomy_alias_for_general_document_expected_type():
    expected = {"document_type": "general_document"}
    actual = {
        "document_type": "memo",
        "category": "purchase_memo",
        "tags": ["purchase_memo"],
        "workflow_metadata": {"taxonomy": {"document_profile": "purchase_memo"}},
    }

    result = compare_expected_actual(expected, actual, {})

    assert "document_type_mismatch" not in {issue["code"] for issue in result["warnings"]}


def test_compare_matches_rows_by_internal_item_code_when_document_code_is_ocr_truncated():
    expected = {
        "no_price_document": True,
        "visible_columns": ["item_name", "internal_item_code"],
        "line_items": [
            {
                "item_name": "SUS304 2T PLATE",
                "internal_item_code": "M-PLT-SUS304-2T-1000X2000",
                "expected_review_flags": ["missing_quantity"],
            }
        ],
    }
    actual = {
        "review_required": True,
        "workflow_metadata": {
            "normalized_review_issues": [{"code": "missing_quantity"}],
        },
        "line_items": [
            {
                "item_name": "SUS3O4 2T PLATE",
                "document_item_code": "M-PLT-SUS304-",
                "internal_item_code": "M-PLT-SUS304-2T-1000X2000",
            }
        ],
    }

    result = compare_expected_actual(expected, actual, {})

    assert "visible_row_missing" not in {issue["code"] for issue in result["warnings"]}


def test_compare_warns_when_required_document_number_is_missing():
    expected = {"document_type": "purchase_order", "document_number": "FAX-VIS-PO-2026-012"}
    actual = {"document_type": "purchase_order", "document_number": None, "line_items": []}

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "WARN"
    assert "document_number_missing" in {issue["code"] for issue in result["warnings"]}


def test_sample_paths_include_images_and_pdfs_only(tmp_path):
    for filename in ("a.pdf", "b.jpg", "c.png", "d.webp", "e.tiff", "ignore.txt", "meta.json"):
        (tmp_path / filename).write_text("sample", encoding="utf-8")

    names = [path.name for path in regression._sample_paths(tmp_path)]

    assert names == ["a.pdf", "b.jpg", "c.png", "d.webp", "e.tiff"]


def test_expected_metadata_jsonl_maps_real_company_smoke_rows(tmp_path):
    rows = [
        {"filename": "DOC-002_tax_invoice_uncropped_photo.png", "document_no": "DOC-002", "document_type": "tax_invoice", "synthetic": True},
        {"filename": "DOC-007_incoming_inspection_uncropped_photo.png", "document_no": "DOC-007", "document_type": "incoming_inspection", "synthetic": True},
        {"filename": "DOC-009_return_credit_uncropped_photo.png", "document_no": "DOC-009", "document_type": "return_credit", "synthetic": True},
        {"filename": "DOC-010_internal_transfer_uncropped_photo.webp", "document_no": "DOC-010", "document_type": "internal_transfer", "synthetic": True},
    ]
    (tmp_path / "expected_metadata.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    metadata = regression._load_expected_metadata(tmp_path)

    assert metadata["DOC-002_tax_invoice_uncropped_photo.png"]["document_type"] == "invoice"
    assert metadata["DOC-007_incoming_inspection_uncropped_photo.png"]["document_type"] == "inspection_report"
    assert metadata["DOC-007_incoming_inspection_uncropped_photo.png"]["no_price_document"] is True
    assert metadata["DOC-009_return_credit_uncropped_photo.png"]["document_type"] == "general_document"
    assert "no_price_document" not in metadata["DOC-009_return_credit_uncropped_photo.png"]
    assert metadata["DOC-010_internal_transfer_uncropped_photo.webp"]["document_type"] == "general_document"
    assert metadata["DOC-010_internal_transfer_uncropped_photo.webp"]["no_price_document"] is True


def test_expected_metadata_jsonl_maps_uncropped_photo_common_fields(tmp_path):
    rows = [
        {
            "filename": "DOC-004_transaction_statement_uncropped_photo.jpg",
            "document_type": "transaction_statement",
            "vendor": "대성정공",
            "customer": "시흥대야점",
            "date": "2026.06.17",
            "total_amount": 11964040,
            "line_items": 7,
            "synthetic": True,
        },
        {
            "filename": "DOC-001_incoming_inspection_uncropped_photo.jpg",
            "document_type": "incoming_inspection",
            "vendor": "신우정밀",
            "customer": "삼광유통",
            "date": "2026.06.02",
            "line_items": 3,
            "synthetic": True,
        },
    ]
    (tmp_path / "expected_metadata.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    metadata = regression._load_expected_metadata(tmp_path)

    statement = metadata["DOC-004_transaction_statement_uncropped_photo.jpg"]
    assert statement["document_type"] == "transaction_statement"
    assert statement["vendor"] == "대성정공"
    assert statement["customer"] == "시흥대야점"
    assert statement["issue_date"] == "2026.06.17"
    assert statement["total_amount"] == 11964040
    assert statement["expected_line_item_min_count"] == 7

    inspection = metadata["DOC-001_incoming_inspection_uncropped_photo.jpg"]
    assert inspection["document_type"] == "inspection_report"
    assert inspection["no_price_document"] is True
    assert inspection["expected_line_item_min_count"] == 3


def test_compare_accepts_dotted_date_expected_against_iso_actual():
    expected = {
        "document_type": "transaction_statement",
        "vendor": "대성정공",
        "customer": "시흥대야점",
        "issue_date": "2026.06.17",
        "total_amount": 11964040,
        "expected_line_item_min_count": 1,
    }
    actual = {
        "document_type": "transaction_statement",
        "vendor_name": "대성정공",
        "customer_name": "시흥대야점",
        "extracted_date": "2026-06-17",
        "extracted_amount": 11964040,
        "line_items": [{"item_name": "S45C PIN"}],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "PASS"


def test_compare_warns_when_required_header_field_is_missing():
    expected = {
        "document_type": "invoice",
        "document_number": "INV-GEN-VIS-2026-011",
        "vendor": "성진전자부품",
        "customer": "네오팩토리",
        "issue_date": "2026-11-11",
        "due_date": "2026-12-11",
    }
    actual = {
        "document_type": "invoice",
        "document_number": "INV-GEN-VIS-2026-011",
        "vendor_name": "성진전자부품",
        "customer_name": "네오팩토리",
        "extracted_date": "2026-11-11",
        "line_items": [],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "WARN"
    assert "due_date_missing" in {issue["code"] for issue in result["warnings"]}


def test_compare_accepts_header_field_aliases():
    expected = {
        "document_type": "purchase_order",
        "document_number": "FAX-VIS-PO-2026-012",
        "vendor": "동진부품",
        "customer": "오성테크",
        "issue_date": "2026-11-12",
        "due_date": "2026-11-26",
    }
    actual = {
        "document_type": "purchase_order",
        "document_number": "FAX-VIS-PO-2026-012",
        "vendor_name": "동진부품",
        "customer_name": "오성테크",
        "extracted_date": "2026-11-12",
        "due_date": "2026-11-26",
        "line_items": [],
    }

    result = compare_expected_actual(expected, actual, {})

    warning_codes = {issue["code"] for issue in result["warnings"]}
    assert "vendor_missing" not in warning_codes
    assert "customer_missing" not in warning_codes
    assert "issue_date_missing" not in warning_codes
    assert "due_date_missing" not in warning_codes


def test_compare_accepts_business_field_header_aliases_and_related_doc_prefix():
    expected = {
        "document_type": "transaction_statement",
        "document_subtype": "return_credit",
        "document_number": "RTN-VIS-2026-008",
        "related_document_number": "DN-VIS-2026-004",
        "subtotal": 11000,
        "tax_amount": 1100,
    }
    actual = {
        "document_type": "general_document",
        "category": "credit_note",
        "document_number": "RTN-VIS-2026-008",
        "workflow_metadata": {
            "business_fields": {"related_document_number": "DN-VIS-2026-004 통화 KRW"},
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
            },
        },
        "line_items": [
            {"supply_amount": 8000, "tax_amount": 800},
            {"supply_amount": 3000, "tax_amount": 300},
        ],
    }

    result = compare_expected_actual(expected, actual, {})

    warning_codes = {issue["code"] for issue in result["warnings"]}
    assert "related_document_number_missing" not in warning_codes
    assert "related_document_number_mismatch" not in warning_codes
    assert "document_subtotal_missing" not in warning_codes
    assert "document_tax_amount_missing" not in warning_codes


def test_compare_accepts_valid_until_in_business_fields():
    expected = {
        "document_type": "quotation",
        "document_number": "QT-VIS-2026-002-ALT",
        "valid_until": "2026-11-30",
    }
    actual = {
        "document_type": "quotation",
        "document_number": "QT-VIS-2026-002-ALT",
        "workflow_metadata": {
            "business_fields": {"valid_until": "2026-11-30"},
        },
        "line_items": [],
    }

    result = compare_expected_actual(expected, actual, {})

    assert "valid_until_missing" not in {issue["code"] for issue in result["warnings"]}


def test_compare_accepts_zero_tax_when_tax_fields_are_absent():
    expected = {
        "document_type": "invoice",
        "currency": "USD",
        "subtotal": 650,
        "tax_amount": 0,
        "total_amount": 650,
        "line_items": [
            {"item_name": "Linear Guide Rail HGW20", "supply_amount": 450},
            {"item_name": "Cable Harness 500", "supply_amount": 110},
            {"item_name": "PCB Connector 12P", "supply_amount": 90},
        ],
    }
    actual = {
        "document_type": "invoice",
        "currency": "USD",
        "extracted_amount": 650,
        "line_items": [
            {"item_name": "Linear Guide Rail HGW20", "supply_amount": 450},
            {"item_name": "Cable Harness 500", "supply_amount": 110},
            {"item_name": "PCB Connector 12P", "supply_amount": 90},
        ],
    }

    result = compare_expected_actual(expected, actual, {})

    assert "document_tax_amount_missing" not in {issue["code"] for issue in result["warnings"]}


def test_compare_accepts_return_credit_policy_type_via_category_metadata():
    expected = {"document_type": "credit_note", "document_number": "RTN-VIS-2026-008"}
    actual = {
        "document_type": "general_document",
        "category": "credit_note",
        "document_number": "RTN-VIS-2026-008",
        "workflow_metadata": {
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
            }
        },
        "line_items": [],
    }

    result = compare_expected_actual(expected, actual, {})

    assert "document_type_mismatch" not in {issue["code"] for issue in result["warnings"]}


def test_compare_accepts_return_credit_policy_type_via_expected_subtype():
    expected = {
        "document_type": "transaction_statement",
        "document_subtype": "return_credit",
        "document_number": "RTN-VIS-2026-008",
    }
    actual = {
        "document_type": "general_document",
        "category": "credit_note",
        "document_number": "RTN-VIS-2026-008",
        "workflow_metadata": {
            "taxonomy": {
                "document_subtype": "credit_note",
                "document_profile": "return_document",
                "document_profiles": ["return_document", "priced_document"],
            }
        },
        "line_items": [],
    }

    result = compare_expected_actual(expected, actual, {})

    assert "document_type_mismatch" not in {issue["code"] for issue in result["warnings"]}


def test_compare_warns_when_expected_line_item_min_count_is_not_met():
    expected = {
        "document_type": "delivery_note",
        "expected_line_item_min_count": 2,
        "no_price_document": True,
    }
    actual = {
        "document_type": "delivery_note",
        "line_items": [{"item_name": "S45C PIN", "quantity": 120}],
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "WARN"
    assert not result["failures"]
    warning = result["warnings"][0]
    assert warning["code"] == "line_item_min_count_not_met"
    assert warning["warn_group"] == "extraction_quality"


def test_compare_splits_no_price_expected_safe_from_quality_warns():
    expected = {
        "document_type": "delivery_note",
        "no_price_document": True,
    }
    actual = {
        "document_type": "delivery_note",
        "review_required": True,
        "tags": ["no_price_document"],
        "line_items": [{"item_name": "S45C PIN", "quantity": 120}],
    }

    result = compare_expected_actual(expected, actual, {})

    warning = result["warnings"][0]
    assert warning["code"] == "no_price_expected_safe"
    assert warning["warn_group"] == "safe_review"


def test_summarize_rows_counts_warn_groups():
    summary = regression.summarize_rows(
        [
            {"status": "WARN", "dangerous_contamination": False, "warnings": [{"warn_group": "extraction_quality"}]},
            {"status": "WARN", "dangerous_contamination": False, "warnings": [{"warn_group": "safe_review"}]},
            {"status": "PASS", "dangerous_contamination": False, "warnings": []},
        ]
    )

    assert summary["actual_quality_warn_count"] == 1
    assert summary["safe_review_warn_count"] == 1


def test_compare_warns_when_expected_quality_flag_is_missing():
    expected = {
        "document_type": "purchase_order",
        "expected_quality_flags": ["document_image_blurry"],
    }
    actual = {
        "document_type": "purchase_order",
        "line_items": [{"item_name": "S45C PIN", "quantity": 120}],
        "workflow_metadata": {"document_quality": {"review_reasons": []}},
    }

    result = compare_expected_actual(expected, actual, {})

    assert result["status"] == "WARN"
    assert "expected_quality_flag_missing" in {issue["code"] for issue in result["warnings"]}


def test_compare_accepts_expected_quality_flag_from_document_quality():
    expected = {
        "document_type": "purchase_order",
        "expected_quality_flags": ["document_image_blurry"],
    }
    actual = {
        "document_type": "purchase_order",
        "line_items": [{"item_name": "S45C PIN", "quantity": 120}],
        "workflow_metadata": {"document_quality": {"review_reasons": ["document_image_blurry"]}},
    }

    result = compare_expected_actual(expected, actual, {})

    assert "expected_quality_flag_missing" not in {issue["code"] for issue in result["warnings"]}
