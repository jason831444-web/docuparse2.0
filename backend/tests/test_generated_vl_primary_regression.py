from __future__ import annotations

from app.scripts.run_generated_vl_primary_regression import compare_expected_actual


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
