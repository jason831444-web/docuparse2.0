from app.services.ai_parsed_document import AiParsedDocumentBuilder


def test_ai_parsed_document_preserves_key_value_table_and_notes():
    builder = AiParsedDocumentBuilder()
    text = """
    자재 이동 요청서
    문서번호 MV-2026-0010
    요청일 2026.06.18
    출고창고 A-01 원자재
    입고창고 B-02 가공
    요청자 최현우
    내부 이동 문서로 금액/세액 없음. 수량 확인 후 처리.
    """
    tables = [
        {
            "source": "paddleocrvl_official_table_html",
            "table_type": "material_transfer_rows",
            "columns": ["No", "품목", "규격", "수량", "단위", "이동사유"],
            "rows": [["1", "S45C PIN", "8X60", "200", "EA", "2라인 긴급 투입"]],
            "bbox": [10, 20, 500, 260],
        }
    ]

    result = builder.build(raw_text=text, tables=tables, document_type_hint="internal_transfer")

    assert result["version"] == 1
    assert result["document_type_hint"] == "internal_transfer"
    key_value = next(section for section in result["sections"] if section["type"] == "key_value")
    normalized_keys = {field["normalized_key"] for field in key_value["fields"]}
    assert {"document_number", "document_date", "source_warehouse", "destination_warehouse", "requester"} <= normalized_keys
    table = next(section for section in result["sections"] if section["type"] == "table")
    assert table["columns"] == ["No", "품목", "규격", "수량", "단위", "이동사유"]
    assert table["rows"][0]["cells"]["품목"] == "S45C PIN"
    assert table["rows"][0]["canonical_cells"]["item_name"] == "S45C PIN"
    notes = next(section for section in result["sections"] if section["type"] == "notes")
    assert any("금액/세액 없음" in note for note in notes["items"])
    assert result["policy"]["amount_allowed"] is False


def test_ai_parsed_document_blocks_amount_candidates_for_no_price_delivery():
    builder = AiParsedDocumentBuilder()
    result = builder.build(
        raw_text="""
        납품서
        문서번호 DN-2026-0003
        단가 미기재 납품서 - 수량 검수용
        합계 95,150
        """,
        tables=[],
        document_type_hint="delivery_note",
    )

    assert result["policy"]["amount_allowed"] is False
    blocked = result["blocked_candidates"]
    assert blocked
    assert blocked[0]["normalized_key"] == "total_amount"
    assert blocked[0]["status"] == "blocked"
    assert blocked[0]["risk"] == "amount_not_allowed_for_document_type"


def test_ai_parsed_document_keeps_unknown_key_value_as_unmapped():
    builder = AiParsedDocumentBuilder()
    result = builder.build(
        raw_text="관리구분 긴급입고\n작업구분 출고전확인",
        tables=[],
        document_type_hint="general_document",
    )

    unmapped = result["unmapped_fields"]
    assert {field["key"] for field in unmapped} >= {"관리구분", "작업구분"}
    assert all(field["status"] == "unmapped" for field in unmapped)


def test_ai_parsed_document_header_ocr_skip_policy_uses_candidates_and_optional_types():
    builder = AiParsedDocumentBuilder()

    candidate_decision = builder.should_skip_header_ocr(raw_text="거래명세서\n문서번호 TS-2026-0008", document_type_hint="transaction_statement")
    assert candidate_decision["skip"] is True
    assert candidate_decision["reason"] == "ai_parsed_document_number_candidate_found"

    optional_decision = builder.should_skip_header_ocr(raw_text="영수증\n거래일시 2026.06.13", document_type_hint="receipt")
    assert optional_decision["skip"] is True
    assert optional_decision["reason"] == "document_type_policy_document_number_optional"
