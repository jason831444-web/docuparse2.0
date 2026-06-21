from app.models.document import DocumentType
from app.services.parser import DocumentParser


def test_purchase_memo_bullet_rows_preserve_visible_items_without_fabricating_missing_price():
    text = """
문서번호:DOC-052
발주 메모
Handwritten-like Purchase Memo
작성일:2026.06.22
아래 자재 입고 요청합니다. 단가는 확인 후 반영
- SUS 볼트 M5x20 300EA 단가 확인필요
-PCB Connector 12P 50EA 단가 620
- S45C PIN 8x60 120EA 단가 350
-AL6061 환봉 10파이 10KG 단가 7200
"""
    parsed = DocumentParser().parse(text, "purchase_memo.jpg")

    assert parsed.document_type == DocumentType.memo
    assert parsed.category == "purchase_memo"
    assert parsed.document_number == "DOC-052"
    assert parsed.issue_date.isoformat() == "2026-06-22"
    assert len(parsed.line_items) == 4
    assert parsed.line_items[0]["item_name"] == "SUS 볼트"
    assert parsed.line_items[0]["quantity"] == 300
    assert "unit_price" not in parsed.line_items[0]


def test_inspection_result_table_rows_with_no_amount_columns_are_kept_as_review_rows():
    text = """
문서번호:DOC-022
입고 검사 기록서
No 품명 Lot/Code 입고수량 검사항목 편정 비고
1 고추장 소스 2kg SAUCE-GJ2 120 외관/치수 재검 입고 보류
2 양파 15kg ONION-15K 120 외관/치수 합격 스크래치 확인
3 M3 육각너트 NUT-M3 300 외관/치수 조건부 합격 이상 없음
4 냉동 닭정육 2kg CHK-2K 10 외관/치수 합격 이상 없음
※ 검사 기록서는 금액이 없는 품질 확인 문서입니다.
"""
    parsed = DocumentParser().parse(text, "incoming_inspection.pdf")

    assert parsed.document_type == DocumentType.inspection_report
    assert parsed.document_number == "DOC-022"
    assert len(parsed.line_items) == 4
    assert parsed.line_items[0]["received_quantity"] == 120
    assert parsed.line_items[0]["inspection_result"] == "재검"
    assert all("line_total" not in item for item in parsed.line_items)


def test_internal_transfer_codes_are_not_confused_with_document_number():
    text = """
문서번호:DOC-007
자재 이동 요청서
No 품목명 내부코드 수량 단위 이동사유
1 S45C PIN 8X60 PIN-8X60 100 EA 긴급요청
2 M3 육각너트 NUT-M3 10 EA 긴급요청
3 냉동 닭정육 2kg CHK-2K 20 BOX 긴급요청
4 고추장 소스 2kg SAUCE-GJ2 10 EA 긴급요청
5 AL6061 환봉 10파이 AL6061-10 20 KG 긴급요청
6 감자 20kg POTATO-20K 100 BOX 생산투입
"""
    parsed = DocumentParser().parse(text, "internal_transfer.jpg")

    assert parsed.document_type == DocumentType.general_document
    assert parsed.category == "internal_transfer"
    assert parsed.document_number == "DOC-007"
    assert len(parsed.line_items) == 6
    assert all(item.get("item_code") != "DOC-007" for item in parsed.line_items)


def test_receipt_rows_and_compact_date_are_extracted_as_review_rows():
    text = """
가온마트
영수증번호:
DOC-041
일자:
20260602
PCB Connector 12P 5EA X 620 3100
Bearing Housing 5EA 12800 64000
공급가액
159636
부가세
15964
합계
175600
감사합니다
"""
    parsed = DocumentParser().parse(text, "receipt.jpg")

    assert parsed.document_type == DocumentType.receipt
    assert parsed.category == "receipt"
    assert parsed.document_number == "DOC-041"
    assert parsed.issue_date.isoformat() == "2026-06-02"
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["line_total"] == 3100


def test_pos_daily_settlement_uses_category_without_forcing_line_items():
    text = """
문서번호:DOC-009
POS 일일정산
Daily Sales Settlement
일자: 20260620
매장: 가온푸드
실판매금액 1266000
순판매금액 1266000
카드합계 835900
"""
    parsed = DocumentParser().parse(text, "pos_daily.jpg")

    assert parsed.document_type == DocumentType.general_document
    assert parsed.category == "pos_daily_settlement"
    assert parsed.document_number == "DOC-009"
    assert parsed.issue_date.isoformat() == "2026-06-20"
    assert parsed.line_items == []
