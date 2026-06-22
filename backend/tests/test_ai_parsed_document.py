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
    assert {"document_number", "request_date", "source_warehouse", "destination_warehouse", "requester"} <= normalized_keys
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


def test_ai_parsed_document_removes_path_lines_and_keeps_party_candidates():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="\n".join(
            [
                "/workspace/docuparse-gpu-test/uploads/vl_remote_uploads/abc-DOC-001.jpg",
                "세금계산서",
                "공급자",
                "상호: (주)삼광유통",
                "사업자번호 123-45-67890",
                "공급받는자",
                "상호: (주)신우정밀",
            ]
        ),
        tables=[],
        document_type_hint="invoice",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    values = {field["normalized_key"]: field["value"] for field in fields if field.get("normalized_key") in {"supplier_name", "customer_name"}}
    evidence = "\n".join(str(field.get("evidence") or "") for field in fields)
    assert values == {"supplier_name": "삼광유통", "customer_name": "신우정밀"}
    assert "/workspace/" not in evidence


def test_ai_parsed_document_top_line_party_is_review_only_without_pos_context():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="대한유통\n납품서\n문서번호 DN-2026-0003",
        tables=[],
        document_type_hint="delivery_note",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    top_line = next(field for field in fields if field["key"] == "상단 거래처 후보")
    assert top_line["value"] == "대한유통"
    assert top_line["status"] == "review_only"
    assert top_line["normalized_key"] == "party_name"


def test_ai_parsed_document_does_not_keep_doc_title_as_party_candidate():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="doc_title\n납품서\n작성일 2026.06.05\\n인수자 서명: ________",
        tables=[],
        document_type_hint="delivery_note",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    assert all(field.get("value") != "doc_title" for field in fields)
    date_field = next(field for field in fields if field.get("normalized_key") == "document_date")
    assert date_field["value"] == "2026.06.05"


def test_ai_parsed_document_separates_reference_and_approval_numbers():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="반품/크레딧 메모\n문서번호 RCM-2026-0009\n원문서 TS-2026-0034\n승인번호 RC-2026-0029",
        tables=[],
        document_type_hint="general_document",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    values = {(field["normalized_key"], field["value"]) for field in fields}
    assert ("document_number", "RCM-2026-0009") in values
    assert ("reference_document_number", "TS-2026-0034") in values
    assert ("approval_number", "RC-2026-0029") in values


def test_ai_parsed_document_does_not_treat_store_as_customer_without_pos_signal():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="자재 이동 요청서\nStore A-01 원자재\n출고창고 A-01 원자재",
        tables=[],
        document_type_hint="internal_transfer",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    assert not any(field.get("normalized_key") == "customer_name" for field in fields)


def test_ai_parsed_document_splits_literal_newline_supplier_customer_blocks():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="세금계산서\\n공급자\\n상호: (주)삼광유통\\n사업자번호 123-45-67890\\n공급받는자\\n상호: (주)신우정밀",
        tables=[],
        document_type_hint="invoice",
    )

    fields = next(section["fields"] for section in result["sections"] if section["type"] == "key_value")
    values = {field["normalized_key"]: field["value"] for field in fields if field.get("normalized_key") in {"supplier_name", "customer_name"}}
    assert values == {"supplier_name": "삼광유통", "customer_name": "신우정밀"}


def test_ai_parsed_document_blocks_titles_and_option_terms_as_party_candidates():
    builder = AiParsedDocumentBuilder()

    result = builder.build(
        raw_text="Internal Transfer\n옵션 긴급 납품 옵션 FAST-DELIVERY 별도협의 미확정\n자재 이동 요청서",
        tables=[],
        document_type_hint="internal_transfer",
    )

    assert not any(section["type"] == "key_value" for section in result["sections"])
    assert result["unmapped_fields"] == []
