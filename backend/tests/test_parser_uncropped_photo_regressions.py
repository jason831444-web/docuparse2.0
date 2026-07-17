from decimal import Decimal

from app.services.parser import DocumentParser


def test_tax_invoice_ocr_header_variants_keep_tax_only_columns():
    raw = """
    문서번호:DOC-044
    세금계산서
    Tax Invoice
    공급자
    상호: (주)태광부품
    공급받는자
    상호: (주)대원식품
    월일 품목 규격 수량 단가 궁금기액 새액
    06.08 S45C PIN 8X60 PIN-8X60 50 350 17,500 1,750
    06.08 양파 15kg ONION-15K 3 22,000 66,000 6,600
    공급가액 83,500
    세액 8,350
    합계 91,850
    """

    parsed = DocumentParser().parse(raw, "DOC-044_tax_invoice_uncropped_photo.jpg")

    assert parsed.document_type.value == "invoice"
    assert parsed.vendor_name == "(주)태광부품"
    assert parsed.customer_name == "(주)대원식품"
    assert parsed.extracted_amount == Decimal("91850")
    assert parsed.line_items[0]["quantity"] == 50
    assert parsed.line_items[0]["unit_price"] == 350
    assert parsed.line_items[0]["supply_amount"] == 17500
    assert parsed.line_items[0]["tax_amount"] == 1750
    assert "line_total" not in parsed.line_items[0]


def test_glued_customer_labels_are_trimmed():
    raw = """
    거래명세서
    공급자
    상호: (주)태광부품
    공급받는자상호: (주)삼광유통작성일: 2026.06.13담당: 박민준 / 구매팀
    No 품목명 규격/코드 수량 단위 단가 합계금액
    1 식자재 감자 20kg POTATO-20K 3 BOX 27,000 81,000
    공급가액 81,000
    부가세 8,100
    총합계 89,100
    """

    parsed = DocumentParser().parse(raw, "DOC-010_transaction_statement_uncropped_photo.pdf")

    assert parsed.vendor_name == "(주)태광부품"
    assert parsed.customer_name == "(주)삼광유통"

    corrected = DocumentParser().parse(
        "세금계산서\n공급자: (주)태광부품\n공급받는자: 비주세움건설\n합계 10,000",
        "DOC-019_tax_invoice_uncropped_photo.jpg",
    )
    assert corrected.customer_name == "세움건설"
    business_label_noise = DocumentParser().parse(
        "세금계산서\n공급자\n상호: (주)우성기계\n공급받는자\n사자변호\n상호: (주)대원식품\n합계 10,000",
        "DOC-023_quotation_uncropped_photo.jpg",
    )
    assert business_label_noise.customer_name == "(주)대원식품"
    assert parsed.extracted_amount == Decimal("89100")


def test_receipt_amounts_use_valid_supply_tax_total_triple():
    raw = """
    한빛문구
    영수증번호:DOC-100
    일자:2026.06.24
    Bearing Housing
    3EA x 12,800 38,400
    공급가액
    부가세 161,650
    16,165
    합계 177,815
    감사합니다
    """

    parsed = DocumentParser().parse(raw, "DOC-100_receipt_uncropped_photo.png")

    assert parsed.document_type.value == "receipt"
    assert parsed.subtotal == Decimal("161650")
    assert parsed.tax == Decimal("16165")
    assert parsed.extracted_amount == Decimal("177815")


def test_missing_footer_purchase_order_recomputes_from_visible_supply_rows():
    raw = """
    문서번호:DOC-040
    발주서
    Purchase Order
    공급자
    상호: (주)가온물류
    공급받는자
    상호: (주)동부프랜차이즈
    No 품목명 품목코드 수량 단위 단가 공급가액
    1 NBR O-Ring 12mm OR-12 3 EA 60 180
    2 HDPE 포장필름 FILM-HDPE 5 ROLL 56,000 280,000
    3 Bearing Housing BRG-HOUSING 100 EA 12,800 1,280,000
    """

    parsed = DocumentParser().parse(raw, "DOC-040_purchase_order_uncropped_photo.jpg")

    assert parsed.vendor_name == "(주)가온물류"
    assert parsed.customer_name == "(주)동부프랜차이즈"
    assert parsed.subtotal == Decimal("1560180")
    assert parsed.tax == Decimal("156018")
    assert parsed.extracted_amount == Decimal("1716198")


def test_split_party_role_rows_keep_customer_role_queue():
    raw = """
    문서번호:DOG
    -081
    공급자
    공급받는지
    상호념
    주우성기계
    상호:
    주코리아맥노리
    문서번호:DOC-081
    견적서
    공급자
    상호: (주)우성기계
    작성일:2026.06.05
    No 품목명 규격/코드 수량 단위 단가 금액
    1 식자재 감자 20kg POTATO-20K 20 BOX 27,000 540,000
    공급가액 1,158,960
    세액 115,896
    예상 합계 1,274,856
    """

    parsed = DocumentParser().parse(raw, "DOC-081_quotation_uncropped_photo.pdf")

    assert parsed.vendor_name == "(주)우성기계"
    assert parsed.customer_name == "(주)코리아팩토리"
    assert parsed.extracted_amount == Decimal("1274856")


def test_party_ocr_dictionary_corrects_common_truncated_names():
    raw = """
    거래명세서
    공급자
    상호:
    주태광부
    공급받는자
    상호:
    주삼광유동
    문서번호:DOC-010
    작성일:2026.06.13
    No 품목명 규격/코드 수량 단위 단가 합계금액
    1 식자재 감자 20kg POTATO-20K 3 BOX 27,000 81,000
    공급가액 81,000
    부가세 8,100
    총합계 89,100
    """

    parsed = DocumentParser().parse(raw, "DOC-010_transaction_statement_uncropped_photo.pdf")

    assert parsed.vendor_name == "(주)태광부품"
    assert parsed.customer_name == "(주)삼광유통"
