from app.services.canonical_schema import (
    canonical_field_for_header,
    canonicalize_official_table_row,
    canonicalize_row,
    expected_column_groups,
    get_document_fields,
    get_exportable_fields,
    get_line_item_fields,
)


def test_header_aliases_map_to_canonical_fields():
    assert canonical_field_for_header("품명") == "item_name"
    assert canonical_field_for_header("Item") == "item_name"
    assert canonical_field_for_header("Description") == "item_name"
    assert canonical_field_for_header("규격") == "specification"
    assert canonical_field_for_header("Unit Price") == "unit_price"
    assert canonical_field_for_header("공급가액") == "supply_amount"
    assert canonical_field_for_header("VAT") == "tax_amount"
    assert canonical_field_for_header("Line Total") == "line_total"
    assert canonical_field_for_header("검사항목") == "inspection_item"
    assert canonical_field_for_header("판정") == "result"
    assert canonical_field_for_header("불량수량") == "defective_quantity"
    assert canonical_field_for_header("알수없는컬럼") is None


def test_official_line_item_row_maps_to_canonical_row():
    row = canonicalize_official_table_row(
        ["품목", "규격", "수량", "단가", "공급가액", "세액", "합계", "비고"],
        ["PCB Connector", "12P", "200", "1,250", "250,000", "25,000", "275,000", "확인"],
        "line_items",
    )

    assert row["item_name"] == "PCB Connector"
    assert row["specification"] == "12P"
    assert row["quantity"] == 200
    assert row["unit_price"] == 1250
    assert row["supply_amount"] == 250000
    assert row["tax_amount"] == 25000
    assert row["line_total"] == 275000
    assert row["note"] == "확인"
    assert row["raw_cells"]["품목"] == "PCB Connector"


def test_inspection_row_maps_and_removes_amount_fields():
    row = canonicalize_official_table_row(
        ["No", "품명", "Lot/Code", "입고수량", "합격수량", "불량수량", "검사항목", "판정", "비고", "금액"],
        ["1", "SUS 볼트 M5x20", "BOLT-M5X20", "120", "119", "1", "외관/치수", "조건부합격", "치수 재확인", "999,999"],
        "incoming_inspection",
    )

    assert row["no"] == 1
    assert row["item_name"] == "SUS 볼트"
    assert row["specification"] == "M5x20"
    assert row["document_item_code"] == "BOLT-M5X20"
    assert row["received_quantity"] == 120
    assert row["accepted_quantity"] == 119
    assert row["defective_quantity"] == 1
    assert row["inspection_item"] == "외관/치수"
    assert row["result"] == "조건부 합격"
    assert row["note"] == "치수 재확인"
    assert "line_total" not in row
    assert "unit_price" not in row
    assert "inspection_report_amount_field_removed" in row["review_flags"]


def test_unknown_header_is_preserved_only_in_raw_cells():
    row = canonicalize_official_table_row(
        ["품명", "관리자메모"],
        ["S45C PIN", "내부 전용"],
        "line_items",
    )

    assert row["item_name"] == "S45C PIN"
    assert "관리자메모" not in row
    assert row["raw_cells"]["관리자메모"] == "내부 전용"


def test_canonicalize_row_supports_dict_input():
    row = canonicalize_row({"품명": "AL6061 판재", "수량": "12", "금액": "237,600"})

    assert row["item_name"] == "AL6061 판재"
    assert row["quantity"] == 12
    assert row["line_total"] == 237600


def test_expected_columns_and_exportable_fields_are_centralized():
    inspection_expected = expected_column_groups("inspection_report")
    assert any("received_quantity" in alternatives for _, alternatives in inspection_expected)
    assert any("inspection_item" in alternatives for _, alternatives in inspection_expected)

    document_values = {field["value"] for field in get_document_fields()}
    line_values = {field["value"] for field in get_line_item_fields()}
    export_values = {field["value"] for field in get_exportable_fields()}

    assert "document_number" in document_values
    assert "received_quantity" in line_values
    assert "line_items.received_quantity" in export_values
    assert "line_items.inspection_result" in export_values
    assert "__blank__" in export_values
    assert "__static__" in export_values
