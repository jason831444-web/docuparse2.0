from decimal import Decimal
from datetime import date

from app.models.document import DocumentType
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata
from app.services.parser import DocumentParser, ParsedDocument
from app.services.vl_candidate_parser import VLCandidateParser


VL_08_TEXT = """견적번호 QT-2026-0808-009 견적일 2026-08-08
공급업체 한성산업 고객사 제일기계
유효기간 2026-08-31 통화 KRW
품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액
고정 플레이트 PLT-FIX-02 120x60x5T EA 2800 280000 28000 308000
스테인리스 브라켓 BRK-SUS-01 50x80x3T 100 EA 1500 150000 15000 165000
총액:473,000
※ 첫 번째 품목 수량 공란: 값 칸은 빈 값으로 남아야 합니다."""


def test_vl_candidate_parser_structures_text_without_confirmed_promotion():
    candidate = VLCandidateParser().parse_text(
        VL_08_TEXT,
        filename="08_image_quote_missing_quantity.pdf",
        manual_visual_check={
            "expected_from_pdf": {
                "document_number": "QT-2026-0808-009",
                "total_amount": "473,000",
                "row_count": "2",
            }
        },
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["candidate_only"] is True
    assert candidate["parser_integrated"] is False
    assert candidate["confirmed_promotion"] is False
    assert candidate["document"]["document_number"] == "QT-2026-0808-009"
    assert candidate["document"]["total"] == "473000"
    assert candidate["line_item_count"] == 2

    first_item = candidate["line_items"][0]
    assert first_item["item_name"] == "고정 플레이트"
    assert "quantity" not in first_item
    assert first_item["unit"] == "EA"
    assert first_item["unit_price"] == 2800
    assert first_item["supply_amount"] == 280000
    assert "missing_quantity" in first_item["validation_warnings"]
    assert "vl_candidate_missing_quantity" in candidate["issue_codes"]


def test_vl_candidate_parser_flags_manual_mismatches_as_review_only_issues():
    candidate = VLCandidateParser().parse_text(
        "COMMERCIAL INVOICE\nInvoice No INV-US-2026-0916-EX\nTotal USD 650.00",
        filename="16_real_commercial_invoice_exchange_rate.pdf",
        manual_visual_check={
            "expected_from_pdf": {
                "document_number": "INV-US-2026-0916-EX",
                "total_amount": "650.00",
                "row_count": "3",
            }
        },
        validation={"status": "warn"},
    )

    assert candidate is not None
    assert candidate["candidate_only"] is True
    assert candidate["parser_integrated"] is False
    assert "vl_candidate_requires_review" in candidate["issue_codes"]
    assert "vl_candidate_row_count_mismatch" not in candidate["issue_codes"]


def test_vl_candidate_parser_flags_low_confidence_source_quality_for_review():
    candidate = VLCandidateParser().parse_text(
        "\n".join([
            "계산서번호 INV-2026-0810-LOW",
            "품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계금액",
            "베어린 한읙징 BRG-H-100 100mm 25 EA 12000 300000 30000 330000",
            "총액:627,000",
            "※ 저품질 스캔: OCR confidence/table confidence 낮음 및 AI escalation 판단 테스트용",
        ]),
        filename="poor_scan_invoice.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_untrusted_source_quality" in candidate["issue_codes"]


def test_vl_candidate_parser_flags_priced_rows_missing_line_amounts():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "COMMERCIAL INVOICE",
                "Invoice No INV-US-2026-0916-EX",
                "Currency USD",
                "No Description Vendor SKU Spec Qty Unit Unit Price Amount",
                "1 Linear Guide Rail HGW20 HGW20-1000 1000mm 10 EA",
                "2 Cable Harness 500 CBL-HAR-500 500mm 50 EA",
                "3 PCB Connector 12P CON-PCB-12P 12P 300 EA",
                "Total USD",
            ]
        ),
        filename="commercial-invoice.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_missing_line_amount" in candidate["issue_codes"]


def test_vl_candidate_parser_flags_delivery_remaining_quantity_hidden():
    candidate = VLCandidateParser().parse_text(
        "\n".join([
            "납품서",
            "문서번호 DN-GEN-2026-003",
            "No 품목명 문서품목코드 규격 발주수량 납품수량 잔량 단위",
            "1 베어링 하우징 BRG-H-100 100mm 80 50 EA",
            "2 S45C PIN 8X60 PIN-8X60 8x60 300 300 EA",
        ]),
        filename="delivery-note-cropped-remaining.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_remaining_quantity_hidden" in candidate["issue_codes"]


def test_vl_candidate_parser_flags_inspection_decision_hidden():
    candidate = VLCandidateParser().parse_text(
        "\n".join([
            "입고검사성적서",
            "문서번호 IQC-GEN-2026-007",
            "No 품목명 Lot No 규격 입고수량 합격수량 불량수량 판정",
            "1 베어링 하우징 LOT-BRG-1007-A 100mm 50 49 1",
            "2 S45C PIN 8X60 LOT-PIN-1007-B 8x60 300 300 0",
        ]),
        filename="inspection-report-cropped-decision.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_inspection_decision_hidden" in candidate["issue_codes"]


def test_vl_candidate_parser_flags_fax_row_boundary_uncertainty():
    candidate = VLCandidateParser().parse_text(
        "\n".join([
            "FAX 발주서",
            "문서번호 FAX-PO-GEN-010",
            "No 품목명 규격 수량 단위 단가 공급가액 세액 합계금액",
            "1 베어링 하우징 100mm 20 EA 8000 160000 16000 176000",
            "2 S45C PIN 8X60 8x60 100 EA 600 60000 6000 66000",
            "※ 팩스형 품질. 176,0OO처럼 O/0 혼동 가능. row boundary review 필요.",
        ]),
        filename="fax-po.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_fax_row_boundary_uncertain" in candidate["issue_codes"]


def test_vl_candidate_parser_restores_single_line_hidden_amount_table_rows():
    candidate = VLCandidateParser().parse_text(
        "거래명세서 TS-GEN-008 공급업체 한빛제조 고객사 오성테크 "
        "품목명 품목코드 규격 수량 단위 단가 공급가액 "
        "SUS304 3T PLATE STS30 1000x2000 3 EA 35000 10 "
        "M8 육각볼트 BOLT-M8 M8x20 2000 EA 120 24 "
        "공급가액 641,000 세액 64,100 총액 705,100",
        filename="statement-hidden-total.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 2
    first, second = candidate["line_items"]
    assert first["item_name"].startswith("SUS304 3T PLATE")
    assert first["quantity"] == 3
    assert first["unit_price"] == 35000
    assert "row_amount_hidden_do_not_infer" in first["validation_warnings"]
    assert second["item_name"] == "M8 육각볼트"
    assert second["quantity"] == 2000
    assert second["unit_price"] == 120
    assert "row_amount_hidden_do_not_infer" in second["validation_warnings"]
    assert "vl_candidate_row_amount_hidden_do_not_infer" in candidate["issue_codes"]


def test_vl_candidate_parser_restores_single_line_rounding_adjustment_rows():
    candidate = VLCandidateParser().parse_text(
        "세금계산서 TI-GEN-009 공급자 정우금속 공급받는자 네오팩토리 "
        "품목명 품목코드 규격 수량 단위 단가 공급가액 "
        "PCB Connector 12P CON-PCB-12P 12P 333 EA 301 100 "
        "조정금액 ADJ-ROUND - -1 EA 1 -1 "
        "공급가액 269,709 세액 26,971 총액 296,680",
        filename="tax-invoice-hidden-row-tax.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 2
    first, second = candidate["line_items"]
    assert first["item_name"] == "PCB Connector 12P"
    assert first["quantity"] == 333
    assert first["unit_price"] == 301
    assert "row_amount_hidden_do_not_infer" in first["validation_warnings"]
    assert second["item_name"] == "조정금액"
    assert second["quantity"] == -1
    assert second["unit_price"] == 1
    assert "row_amount_hidden_do_not_infer" in second["validation_warnings"]


def test_vl_candidate_parser_uses_pre_repair_rows_when_visual_amount_column_is_hidden():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "거래명세서",
                "문서번호 TS-GEN-2026-008",
                "공급업체 한빛제조",
                "고객사 오성테크",
                "No 품목명 품목코드 규격 수량 단위 단가 공",
                "1 SUS304 3T PLATE 1000x2000 3 EA 35000",
                "2 M8 육각볼트 M8x20 2000 EA 120",
                "공급가액 641,000",
                "세액 64,100",
                "총액 705,100",
                "Text layer contains hidden row totals; visual confirmed values must not pretend those columns are visible.",
            ]
        ),
        filename="statement-hidden-total.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 2
    first, second = candidate["line_items"]
    assert first["quantity"] == 3
    assert first["unit_price"] == 35000
    assert "supply_amount" not in first
    assert "row_amount_hidden_do_not_infer" in first["validation_warnings"]
    assert second["quantity"] == 2000
    assert second["unit_price"] == 120
    assert "supply_amount" not in second
    assert "row_amount_hidden_do_not_infer" in second["validation_warnings"]


def test_vl_candidate_parser_preserves_negative_adjustment_amount_when_positive_rows_are_hidden():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "전자 세금계산서",
                "문서번호 TAX-GEN-2026-009",
                "No 품목명 품목코드 규격 수량 단위 단가 공급",
                "1 PCB Connector 12P CON-PCB-12P 12P 333 EA 301",
                "2 조정금액 ROUND-ADJ 원단위 조정 1 식 -1 -1",
                "공급가액 269,709",
                "세액 26,971",
                "총액 296,680",
                "Row supply amount for the first item is visually clipped; summary subtotal/tax/total remain visible.",
            ]
        ),
        filename="tax-invoice-hidden-row-tax.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 2
    first, second = candidate["line_items"]
    assert first["quantity"] == 333
    assert first["unit_price"] == 301
    assert "supply_amount" not in first
    assert "row_amount_hidden_do_not_infer" in first["validation_warnings"]
    assert second["item_name"] == "조정금액"
    assert second["supply_amount"] == -1


def test_vl_candidate_parser_blocks_inspection_header_row_from_items():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "입고검사성적서",
                "검사번호 IQC-2026-0918-044",
                "No 품목명 Lot No 규격 입고수량 합격수량 불량수량",
                "1 베어링 하우징 LOT-BRG-0918-A 100mm 50 49 1",
            ]
        ),
        filename="inspection-report.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_header_row_as_item" not in candidate["issue_codes"]
    assert not any("품목명 Lot No" in item.get("item_name", "") for item in candidate["line_items"])


def test_vl_candidate_parser_flags_return_credit_type_uncertainty():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "반품 / 차감 요청서",
                "문서번호 RTN-2026-0919-011",
                "관련납품서 DN-2026-0914-2F",
                "1 베어링 하우징 100mm 1 EA 8000 8000 800 8800",
                "차감 합계 12100",
            ]
        ),
        filename="return-credit.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_return_credit_type_uncertain" in candidate["issue_codes"]


def test_vl_candidate_parser_suppresses_negative_document_level_amounts():
    parser = VLCandidateParser()
    parsed = ParsedDocument(
        document_type=DocumentType.general_document,
        document_number="RTN-GEN-2026-006",
        subtotal=Decimal("-3"),
        extracted_amount=Decimal("12100"),
    )

    compact = parser._compact_document(parsed)
    issues = parser._structural_issues(parsed, "반품/차감요청서 관련납품서 DN-GEN-2026-003 총액 12100")

    assert compact["subtotal"] is None
    assert compact["total"] == "12100"
    assert "vl_candidate_negative_document_amount_suppressed" in [issue["code"] for issue in issues]


def test_vl_candidate_parser_does_not_flag_inspection_note_as_return_credit():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "입고검사성적서",
                "검사번호 IQC-2026-0918-044",
                "No 품목명 Lot No 규격 입고수량 합격수량 불량수량",
                "1 베어링 하우징 LOT-BRG-0918-A 100mm 50 49 1",
                "비고: 불량 1EA는 반품 예정",
            ]
        ),
        filename="inspection.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_return_credit_type_uncertain" not in candidate["issue_codes"]


def test_vl_candidate_parser_does_not_warn_for_safe_internal_transfer_broad_type():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "사업장간 자재 이동 요청서",
                "문서번호 TRF-2026-0922-002",
                "No 품목명 내부품목코드 규격 요청수량 단위",
                "1 SUS304 2T PLATE M-PLT-SUS304-2T-1000X20001000x2000 2 EA",
            ]
        ),
        filename="internal-transfer.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_internal_transfer_type_uncertain" not in candidate["issue_codes"]


def test_vl_candidate_parser_parses_inspection_rows_with_decision_column_without_header_item():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "입고검사성적서",
                "공급업체 대영부품 고객사 오성테크",
                "검사번호 IQC-VIS-2026-007 입고일 2026-11-07",
                "No 품목명 Lot No 규격 입고수량 합격수량 불량수량 판정",
                "1 베어링 하우징 LOT-BRG-VIS-A 100mm 50 49 1 조건부 합격",
                "2 S45C PIN 8X60 LOT-PIN-VIS-B 8x60 300 300 0 합격",
                "비고: 불량 1EA는 반품 예정",
            ]
        ),
        filename="inspection-full-visible.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    names = [item["item_name"] for item in candidate["line_items"]]
    assert names == ["베어링 하우징", "S45C PIN 8X60"]
    assert not any("품목명 Lot No" in name for name in names)
    first, second = candidate["line_items"]
    assert first["received_quantity"] == 50
    assert first["accepted_quantity"] == 49
    assert first["rejected_quantity"] == 1
    assert first["inspection_result"] == "조건부 합격"
    assert second["received_quantity"] == 300
    assert second["accepted_quantity"] == 300
    assert second["rejected_quantity"] == 0
    assert second["inspection_result"] == "합격"
    assert "vl_candidate_header_row_as_item" not in candidate["issue_codes"]
    assert "vl_candidate_return_credit_type_uncertain" not in candidate["issue_codes"]


def test_vl_candidate_parser_strips_standalone_row_number_prefix_but_keeps_model_numbers():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "세금계산서 / INVOICE",
                "계산서번호 INV-GEN-VIS-2026-011",
                "No Item Description Vendor SKU Specification Qty Unit Unit Price Subtotal Tax Total",
                "1 PCB Connector 12P CON-PCB-12P 12P 1,500 EA 300 450,000 45,000 495,000",
                "2 Cable Harness 500 CBL-HAR-500 500mm 350 EA 2,200 770,000 77,000 847,000",
                "3 AL6061 환봉 10파이 AL6061-ROD-10 10mm x 3000 30 EA 8,000 240,000 24,000 264,000",
                "4 2PIN Connector CON-2PIN 2PIN 10 EA 100 1,000 100 1,100",
                "Vendor SKU 컬럼을 별도 item row로 만들지 말 것.",
            ]
        ),
        filename="vendor-sku-invoice.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    names = [item["item_name"] for item in candidate["line_items"]]
    assert names[:3] == ["PCB Connector 12P", "Cable Harness 500", "AL6061 환봉 10파이"]
    assert "2PIN Connector" in names
    assert not any(name.startswith(("1 ", "2 ", "3 ", "4 ")) for name in names)
    assert not any(name.lower() == "vendor sku" for name in names)


def test_vl_candidate_parser_extracts_generated_document_number_patterns():
    parser = VLCandidateParser().parser

    assert parser._extract_document_number("거래명세서번호 TS-VIS-2026-005-MON") == "TS-VIS-2026-005-MON"
    assert parser._extract_document_number("Invoice No INV-US-VIS-2026-006-EX") == "INV-US-VIS-2026-006-EX"
    assert parser._extract_document_number("팩스 발주번호 FAX-VIS-PO-2026-012") == "FAX-VIS-PO-2026-012"


def test_vl_candidate_parser_maps_statement_supply_tax_total_without_unit_price():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "거래명세서",
                "거래명세서번호 TS-VIS-2026-005-MON",
                "통화 KRW",
                "No 거래일 품목명 규격 수량 단위 공급가액 세액 합계",
                "1 11-05 SUS304 3T PLATE 1000x2000 3 EA 105000 10500 115500",
                "2 11-05 AL6061 환봉 10파이 3000mm 12 EA 216000 21600 237600",
                "공급가액 641,000 세액 64,100 총액 705,100",
            ]
        ),
        filename="transaction-statement-full-visible.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_number"] == "TS-VIS-2026-005-MON"
    first = candidate["line_items"][0]
    assert "unit_price" not in first
    assert first["supply_amount"] == 105000
    assert first["tax_amount"] == 10500
    assert first["line_total"] == 115500


def test_vl_candidate_parser_preserves_visible_commercial_invoice_amount_as_line_total():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "COMMERCIAL INVOICE",
                "Invoice No INV-US-VIS-2026-006-EX",
                "Currency USD",
                "No Description Vendor SKU Spec Qty Unit Unit Price Amount",
                "1 Linear Guide Rail HGW20 HGW20-1000 1000mm 10 EA 45.00 450.00",
                "2 Cable Harness 500 CBL-HAR-500 500mm 50 EA 2.20 110.00",
                "Subtotal USD 650.00",
                "Tax 0.00",
                "Total USD 650.00",
            ]
        ),
        filename="commercial-invoice-full-visible.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_number"] == "INV-US-VIS-2026-006-EX"
    first = candidate["line_items"][0]
    assert first["unit_price"] == 45
    assert first["supply_amount"] == 450
    assert first["line_total"] == 450
    assert "row_amount_hidden_do_not_infer" not in first.get("validation_warnings", [])


def test_vl_candidate_parser_preserves_code_first_priced_purchase_order_rows():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "발주서",
                "문서번호 PO-2026-0001",
                "No 품목코드 품명 규격 수량 단가 공급가액 세액 합계",
                "1 HB-AX-102 S45C PIN 8X60 120 350 42,000 4,200 46,200",
                "2 HB-BT-520 SUS 볼트 M5X20 300 90 27,000 2,700 29,700",
                "3 HB-WH-014 평와서 M5 500 35 17,500 1,750 19,250",
                "공급가액 86,500 세액 8,650 합계 95,150",
            ]
        ),
        filename="purchase-order-code-first.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"]
    assert first["document_item_code"] == "HB-AX-102"
    assert first["item_name"] == "S45C PIN"
    assert first["specification"] == "8x60"
    assert first["quantity"] == 120
    assert first["unit_price"] == 350
    assert first["supply_amount"] == 42000
    assert second["item_name"] == "SUS 볼트"
    assert second["quantity"] == 300
    assert third["item_name"] == "평와서"
    assert third["line_total"] == 19250


def test_vl_candidate_parser_accepts_supply_header_ocr_variant_without_line_total_column():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "세금계산서",
                "문서번호 INV-2026-0002",
                "발일 품목 규격 수량 단가 공급기록 세액",
                "06/12 PCB Connector 12P 200 1,250 250,000 25,000",
                "06/12 Cable Harness 500mm 80 2,800 224,000 22,400",
                "06/12 AL6061 환봉 10파이 30 8,500 255,000 25,500",
                "공급가액 합계 729,000 세액 합계 72,900 청구금액 801,900",
            ]
        ),
        filename="tax-invoice-supply-record-header.png",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 3
    first = candidate["line_items"][0]
    assert first["item_name"] == "PCB Connector"
    assert first["specification"] == "12P"
    assert first["quantity"] == 200
    assert first["unit_price"] == 1250
    assert first["supply_amount"] == 250000
    assert first["tax_amount"] == 25000
    assert "line_total" not in first
    assert "line_total_column_not_visible" in first["validation_warnings"]
    assert "vl_candidate_invalid_line_total" not in candidate["issue_codes"]


def test_vl_candidate_parser_preserves_textual_spec_no_price_delivery_rows():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "납품서",
                "문서번호 DN-2026-0003",
                "※ 단가 미기재 납품서-수량 검수용",
                "No 품목명 규격 수량 단위 비고",
                "1 S45C PIN 8X60 500 EA 입고대기",
                "2 SUS 볼트 M5X20 1,000 EA 정상",
                "3 평와서 M5 2,000 EA 정상",
                "4 포장박스 중형 40 BOX 반품 2박스 제외",
            ]
        ),
        filename="delivery-note-no-price-text-spec.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 4
    last = candidate["line_items"][-1]
    assert last["item_name"] == "포장박스"
    assert last["specification"] == "중형"
    assert last["quantity"] == 40
    assert last["unit"] == "BOX"
    assert "unit_price" not in last
    assert "supply_amount" not in last


def test_vl_candidate_parser_flags_generated_foreign_schedule_noise_as_output_corruption():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "자재 이동 요청서",
                "MR-2026-0010",
                "序号 时间 状态",
                *[f"{index} 2026.06.{17 + index:02d} 正常" for index in range(1, 9)],
                "No 番号 デリ 今替 단위 이용사용",
                "1 S45C PIN BX60 200 EA 가공대기",
                "2 AL6061 컴퓨 10000 50 EA 가공대기",
            ]
        ),
        filename="internal-transfer-corrupted-vl.webp",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_output_corruption" in candidate["issue_codes"]


def test_text_layer_parser_can_supply_fax_header_fields_for_vl_reconciliation():
    parsed = DocumentParser().parse(
        "\n".join(
            [
                "팩스 발주서",
                "공급업체",
                "동진부품",
                "고객사",
                "오성테크",
                "발주번호",
                "FAX-VIS-PO-2026-012",
                "발행일",
                "2026-11-12",
                "납기일",
                "2026-11-26",
                "No 품목명 품목코드 규격 수량 단위 단가 공급가액 세액 합계",
                "3 M8 볼트 / 와셔 SET SE-M8 M8 1000 SET 160 160000 16000 176000",
            ]
        ),
        "fax.pdf",
    )

    assert parsed.document_number == "FAX-VIS-PO-2026-012"
    assert parsed.vendor_name == "동진부품"
    assert parsed.customer_name == "오성테크"
    assert parsed.issue_date == date(2026, 11, 12)
    assert parsed.due_date == date(2026, 11, 26)


def test_text_layer_parser_can_supply_invoice_payment_due_date_for_vl_reconciliation():
    text = "\n".join(
        [
            "세금계산서 / INVOICE",
            "계산서번호",
            "INV-GEN-VIS-2026-011",
            "발행일",
            "2026-11-11",
            "지급기한",
            "2026-12-11",
        ]
    )

    parsed = DocumentParser().parse(text, "invoice.pdf")

    assert parsed.due_date == date(2026, 12, 11)


def test_vl_candidate_parser_structures_handwritten_transaction_statement_array():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "거래멈세서",
                "업체: 대한정밀",
                "받는곳: 한빛산업",
                "날짜: 26.6.15",
                r"$$ \begin{array}{l} 545C \quad PIN\quad 8\times60 \quad 120EA \quad 350\\ \text{육각분트 } M6\times25 \quad 500 \quad 45\\ \text{스프장와야 } M6 \quad 500 \quad 12\\ \text{AL 브라켓 } 40\times80 \quad 30 \quad 2800 \end{array} $$",
                "display_formula",
                "站到：152,000",
                "비고:2박스 납품/일부 급함",
            ]
        ),
        filename="handwritten-statement.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "transaction_statement"
    assert candidate["document"]["vendor_name"] == "대한정밀"
    assert candidate["document"]["customer_name"] == "한빛산업"
    assert candidate["document"]["issue_date"] == "2026-06-15"
    assert candidate["document"]["total"] == "152000"
    assert candidate["line_item_count"] == 4
    first = candidate["line_items"][0]
    assert first["item_name"] == "S45C PIN"
    assert first["specification"] == "8x60"
    assert first["quantity"] == 120
    assert first["unit"] == "EA"
    assert first["unit_price"] == 350
    assert "line_total" not in first
    assert "line_total_not_visible_do_not_infer" in first["validation_warnings"]


def test_vl_candidate_parser_structures_handwritten_inspection_note():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "간이 검사 기록",
                "거리처: 한성기계",
                "날짜: 26.6.18",
                "품명: SUS 핀 6×40",
                "검사수감: 50개",
                "치수 이상없음",
                "표면 스크레치 2개",
                "합격 48 / 보득 2",
                "담당: 최주임",
            ]
        ),
        filename="handwritten-inspection.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "inspection_report"
    assert candidate["document"]["vendor_name"] == "한성기계"
    assert candidate["document"]["issue_date"] == "2026-06-18"
    assert candidate["line_item_count"] == 1
    item = candidate["line_items"][0]
    assert item["item_name"] == "SUS 핀"
    assert item["specification"] == "6x40"
    assert item["quantity"] == 50
    assert item["received_quantity"] == 50
    assert item["accepted_quantity"] == 48
    assert "unit_price" not in item
    assert "supply_amount" not in item


def test_vl_candidate_parser_structures_handwritten_delivery_rows_without_amounts():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "납품서",
                "삼염금속→태성테크",
                "26/6/15",
                "3) S45C SHAFT $ 12 x 150 $ 40",
                "4) AL PLATE 3T $ 50 x 100 $ 25",
                "종 4종",
                "담당:김부장",
                "비고:오전 입고 처리",
            ]
        ),
        filename="handwritten-delivery.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "delivery_note"
    assert candidate["document"]["issue_date"] == "2026-06-15"
    assert candidate["line_item_count"] == 2
    first = candidate["line_items"][0]
    assert first["item_name"] == "S45C SHAFT"
    assert first["specification"] == "12x150"
    assert first["quantity"] == 40
    assert "unit_price" not in first
    assert "supply_amount" not in first


def test_vl_candidate_parser_structures_handwritten_material_list_without_amounts():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "자제 리스크",
                "현장: 2공장",
                "26.6.19",
                "알루미늄 판 2T 15장",
                "SUS 봉제 10파이 8본",
                "육각볼트 M6×20 400",
                "스프링와샤 M6 400",
                "케이블 타이 2봉",
                "메모: 부족분 먼저 구매",
            ]
        ),
        filename="handwritten-material-list.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["vendor_name"] == "2공장"
    assert candidate["document"]["issue_date"] == "2026-06-19"
    assert candidate["line_item_count"] == 5
    assert candidate["line_items"][0]["item_name"] == "알루미늄 판"
    assert candidate["line_items"][0]["specification"] == "2T"
    assert candidate["line_items"][0]["quantity"] == 15
    assert candidate["line_items"][0]["unit"] == "장"
    assert candidate["line_items"][1]["item_name"] == "SUS 봉재"
    assert candidate["line_items"][1]["unit"] == "본"
    assert candidate["line_items"][2]["item_name"] == "육각볼트"
    assert candidate["line_items"][2]["specification"] == "M6x20"
    assert all("unit_price" not in item for item in candidate["line_items"])
    assert all("supply_amount" not in item for item in candidate["line_items"])


def test_vl_candidate_parser_suppresses_currency_for_amountless_handwritten_delivery():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "납품서",
                "업체: 동성산업",
                "날짜 26.6.16",
                "S45C 봉재 20파이 6본",
                "육각너트 M8 1000",
                "평와샤 M8 1000",
                "비고: 금액 없는 수량 확인용 문서",
                "Currency USD",
            ]
        ),
        filename="handwritten-delivery-note.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "delivery_note"
    assert candidate["document"]["currency"] is None
    assert candidate["line_item_count"] >= 1
    assert all("unit_price" not in item for item in candidate["line_items"])
    assert all("supply_amount" not in item for item in candidate["line_items"])


def test_parser_does_not_promote_quantity_only_value_as_vendor_name():
    parsed = DocumentParser().parse(
        "\n".join(
            [
                "발주 메모",
                "업체:",
                "6본",
                "봉재",
                "20파이",
                "S45C",
                "육각너트 M8 1000",
                "담당: 이과장",
            ]
        ),
        "handwritten-order-memo.txt",
    )

    assert parsed.vendor_name != "6본"


def test_vl_candidate_parser_structures_handwritten_transaction_statement_rows():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "거래명세서",
                "업체: 대한정밀",
                "받는곳: 한빛산업",
                "날짜: 26.6.15",
                "S45C PIN 8x60 120EA 350",
                "육각볼트 M6x25 500 45",
                "스프링와샤 M6 500 12",
                "AL 브라켓 40x80 30 2800",
                "합계: 152,000",
                "비고: 2박스 납품 / 일부 급함",
            ]
        ),
        filename="handwritten-transaction-statement.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "transaction_statement"
    assert candidate["line_item_count"] == 4
    first = candidate["line_items"][0]
    assert first["item_name"] == "S45C PIN"
    assert first["specification"] == "8x60"
    assert first["quantity"] == 120
    assert first["unit"] == "EA"
    assert first["unit_price"] == 350
    assert "line_total_not_visible_do_not_infer" in first["validation_warnings"]
    assert "vl_candidate_handwritten_vl_candidate" in candidate["issue_codes"]


def test_vl_candidate_parser_does_not_promote_settlement_summary_lines_as_items():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "일일 정산",
                "날짜: 26.6.18",
                "김밥 120 3000",
                "라면 80 4500",
                "실 판매금액 968,400",
                "판매 총액 968,400",
                "+ 판입금액 0",
                "+ 온라인결제 240,000",
            ]
        ),
        filename="blurry-pos-daily-settlement.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    names = [item["item_name"] for item in candidate["line_items"]]
    assert "김밥" in names
    assert "라면" in names
    assert all("판매" not in name and "온라인결제" not in name and "판입금액" not in name for name in names)


def test_vl_candidate_parser_structures_handwritten_no_price_delivery_rows_without_amounts():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "납품서",
                "삼영금속 -> 태성테크",
                "26/6/15",
                "1) SUS 볼트 M5x20 300",
                "2) 평와샤 M5 300",
                "3) S45C SHAFT 12X150 40",
                "4) AL PLATE 3T 50x100 25",
                "총 4종",
                "비고: 오전 입고 처리",
            ]
        ),
        filename="handwritten-delivery-note.jpg",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "delivery_note"
    assert candidate["line_item_count"] == 4
    for item in candidate["line_items"]:
        assert item.get("quantity") is not None
        assert "unit_price" not in item
        assert "supply_amount" not in item
        assert "line_total" not in item
        assert "handwritten_amount_missing_or_not_applicable" in item["validation_warnings"]


def test_vl_candidate_parser_keeps_structured_table_over_handwritten_fallback_for_tax_invoice():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "세금계산서",
                "계산서번호 INV-2026-0002",
                "품목 규격 수량 단가 공급가액 세액 합계금액",
                "06/12 PCB Connector 12P 200 1,250 250,000 25,000 275,000",
                "06/12 Cable Harness 500mm 80 2,800 224,000 22,400 246,400",
                "06/12 AL6061 환봉 10파이 30 8,500 255,000 25,500 280,500",
                "청구금액 801,900",
            ]
        ),
        filename="tax-invoice-uncropped.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 3
    names = [item["item_name"] for item in candidate["line_items"]]
    assert names == ["PCB Connector", "Cable Harness", "AL6061 환봉 10파이"]
    assert not any("청구금액" in name for name in names)
    first = candidate["line_items"][0]
    assert first["quantity"] == 200
    assert first["unit_price"] == 1250
    assert first["supply_amount"] == 250000
    assert first["tax_amount"] == 25000
    assert first["line_total"] == 275000
    assert "handwritten_vl_candidate" not in first.get("validation_warnings", [])


def test_vl_candidate_parser_keeps_structured_option_quote_rows():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "태광테크",
                "견적서",
                "견적번호 QT-2026-0005",
                "수신 대성정공",
                "품목 규격 수량 단가 공급가액 세액 합계금액",
                "A 산업용 센서 SN-240 10 38,000 380,000 38,000 418,000",
                "B 컨트롤 박스 CB-9 2 210,000 420,000 42,000 462,000",
                "총액 880,000",
            ]
        ),
        filename="quotation-uncropped.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["vendor_name"] == "태광테크"
    assert candidate["document"]["customer_name"] == "대성정공"
    assert candidate["line_item_count"] == 2
    first, second = candidate["line_items"]
    assert first["item_name"] == "A 산업용 센서"
    assert first["document_item_code"] == "SN-240"
    assert first["quantity"] == 10
    assert first["line_total"] == 418000
    assert second["item_name"] == "B 컨트롤 박스"
    assert second["quantity"] == 2
    assert second["line_total"] == 462000
    assert not any("handwritten_vl_candidate" in item.get("validation_warnings", []) for item in candidate["line_items"])


def test_vl_candidate_parser_keeps_return_credit_rows_and_suppresses_summary_row():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "반품/크레딧 메모",
                "문서번호 RCM-2026-0009",
                "품목 규격 수량 단가 공급가액 세액 합계금액",
                "스프링 와셔 M6 2 14,500 -29,000 -2,900 -31,900",
                "반품 운송비 - 1 5,000 -5,000 -500 -5,500",
                "조정 합계 -34,100",
            ]
        ),
        filename="return-credit.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["line_item_count"] == 2
    names = [item["item_name"] for item in candidate["line_items"]]
    assert names == ["스프링 와셔", "반품 운송비"]
    assert not any("조정" in name or "합계" in name for name in names)
    assert candidate["line_items"][0]["line_total"] == -31900
    assert candidate["line_items"][1]["line_total"] == -5500


def test_vl_candidate_parser_structures_internal_transfer_rows_with_internal_codes():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "자재 이동 요청서",
                "문서번호 MV-2026-0010",
                "No 품목 내부품목코드 규격 요청수량 단위 비고",
                "1 S45C PIN P-PIN-S45C-08X60 8x60 200 EA 2공장 요청",
                "2 AL6061 환봉 M-BAR-AL6061-10MM-3000 10파이 50 EA 가공 대기",
                "3 케이블 타이 E-CABLE-TIE 100mm 6 CAN 2층 요청",
            ]
        ),
        filename="internal-transfer.pdf",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_number"] == "MV-2026-0010"
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"]
    assert first["item_name"] == "S45C PIN"
    assert first["document_item_code"] == "P-PIN-S45C-08X60"
    assert first["quantity"] == 200
    assert first["requested_quantity"] == 200
    assert second["item_name"] == "AL6061 환봉"
    assert second["document_item_code"] == "M-BAR-AL6061-10MM-3000"
    assert second["specification"] == "10파이"
    assert second["quantity"] == 50
    assert third["item_name"] == "케이블 타이"
    assert third["document_item_code"] == "E-CABLE-TIE"
    assert third["quantity"] == 6
    assert not any("unit_price" in item or "supply_amount" in item for item in candidate["line_items"])


def test_vl_candidate_parser_structures_internal_transfer_rows_without_internal_codes():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "자재 이동 요청서",
                "문서번호",
                "MV-2026-0010",
                "No 품목 규격 수량 단위 이동사유",
                "1 S45C PIN 8X60 200 EA 2라인 긴급 투입",
                "2 AL6061 환봉 10파이 50 EA 가공 대기",
                "3 절삭유 4L 6 CAN 공용 소모품",
                "※ 내부 이동 문서로 금액/세액 없음. 수량 확인 후 처리.",
            ]
        ),
        filename="internal-transfer-no-code.webp",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_number"] == "MV-2026-0010"
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"]
    assert first["item_name"] == "S45C PIN"
    assert first["specification"] == "8x60"
    assert first["quantity"] == 200
    assert second["item_name"] == "AL6061 환봉"
    assert "document_item_code" not in second
    assert second["specification"] == "10파이"
    assert second["quantity"] == 50
    assert third["item_name"] == "절삭유"
    assert third["specification"] == "4L"
    assert third["quantity"] == 6
    assert third["unit"] == "CAN"
    assert not any("unit_price" in item or "supply_amount" in item for item in candidate["line_items"])


def test_vl_candidate_parser_normalizes_document_number_with_ocr_space_inside_suffix():
    parsed = DocumentParser().parse(
        "\n".join(
            [
                "COMMERCIAL INVOICE",
                "문서번호",
                "INV-US-GEN- OO4",
                "공급업체",
                "Global Motion Parts LLC",
            ]
        ),
        "hidden-amount.pdf",
    )

    assert parsed.document_number == "INV-US-GEN-004"


def test_vl_candidate_parser_parses_tax_invoice_rows_without_visible_line_total():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "세금계산서",
                "문서번호 INV-2026-0002",
                "작성일자 2026.06.12",
                "공급자 동해산업",
                "공급받는자 대성정공",
                "월일 품목 규격 수량 단가 공급가액 세액",
                "06/12 PCB Connector 12P 200 1,250 250,000 25,000",
                "06/12 Cable Harness 500mm 80 2,800 224,000 22,400",
                "06/12 AL6061 환봉 10파이 30 8,500 255,000 25,500",
                "공급가액 합계 729,000",
                "세액 합계 72,900",
                "청구금액 801,900",
            ]
        ),
        filename="tax-invoice-visible-tax-only.png",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "invoice"
    assert candidate["document"]["document_number"] == "INV-2026-0002"
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"]
    assert first["item_name"] == "PCB Connector"
    assert first["specification"] == "12P"
    assert first["quantity"] == 200
    assert first["unit_price"] == 1250
    assert first["supply_amount"] == 250000
    assert first["tax_amount"] == 25000
    assert "line_total" not in first
    assert "line_total_column_not_visible" in first["validation_warnings"]
    assert second["item_name"] == "Cable Harness"
    assert second["specification"] == "500mm"
    assert third["item_name"] == "AL6061 환봉 10파이"
    assert third["quantity"] == 30
    assert "vl_candidate_invalid_line_total" not in candidate["issue_codes"]


def test_vl_candidate_parser_parses_inspection_rows_without_lot_column():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "대성정공품질팀",
                "입고 검사 기록서",
                "문서번호 IOC-2026-0007",
                "검사일 2026.06.15",
                "협력사 한빛정밀",
                "검사자 박지훈",
                "No 품목 규격 입고수량 합격 불량 판정 비고",
                "1 베어링 하우징 BH-220 80 78 2 조건부합격 표면 흠집",
                "2 S45C PIN 8X60 300 300 0 합격",
                "3 SUS 볼트 M5X20 500 497 3 재검 나사산 확인",
                "검사 의견: 불량 수량은 별도 격리 후 협력사 통보. 금액 항목 없음.",
            ]
        ),
        filename="incoming-inspection-visible.png",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_type"] == "inspection_report"
    assert candidate["document"]["document_number"] == "IOC-2026-0007"
    assert candidate["document"]["vendor_name"] == "한빛정밀"
    assert candidate["document"]["customer_name"] == "대성정공품질팀"
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"]
    assert first["item_name"] == "베어링 하우징"
    assert first["specification"] == "BH-220"
    assert first["received_quantity"] == 80
    assert first["accepted_quantity"] == 78
    assert first["rejected_quantity"] == 2
    assert first["inspection_result"] == "조건부합격"
    assert second["item_name"] == "S45C PIN"
    assert second["specification"] == "8x60"
    assert second["received_quantity"] == 300
    assert second["rejected_quantity"] == 0
    assert third["item_name"] == "SUS 볼트"
    assert third["specification"] == "M5x20"
    assert third["inspection_result"] == "재검"
    assert all("unit_price" not in item and "line_total" not in item for item in candidate["line_items"])


def test_vl_candidate_parser_flags_foreign_script_item_noise_in_korean_manufacturing_doc():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "대성정공 생산관리",
                "자재 이동 요청서",
                "문서번호 MV-2026-0010",
                "요청일 2026.06.18",
                "출고창고 A-01 창고",
                "입고창고 B-02 가공",
                "No 품목 규격 수량 단위 이동사유",
                "1 S45C PIN 8x60 200 EA 긴급 투입",
                "2 番号 AL6061 10파이 50 EA 가공 대기",
                "3 절삭유 4L 6 CAN 교체 소모품",
                "내부 이동 문서로 금액/세액 없음.",
            ]
        ),
        filename="internal-transfer-blurry.webp",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert "vl_candidate_foreign_script_item_noise" in candidate["issue_codes"]
    assert candidate["document"]["document_number"] == "MV-2026-0010"


def test_vl_candidate_parser_normalizes_blurry_manufacturing_transfer_ocr_noise():
    candidate = VLCandidateParser().parse_text(
        "\n".join(
            [
                "대성정공 생산관리",
                "자재 이동 요청서",
                "문명 MV.2026-0010",
                "요청일 2026.06.18",
                "No 图号 규격 수량 단위 이동시킴",
                "1 S45C PIN BX60 200 EA 가공 대기",
                "2 AL6061 환불 10박이 50 EA 규명 소모율",
                "3 발사위 4L 6 CAN 공통 소모율",
                "내부 이동 문서로 금액/세액 없음. 수량 확인 후 처리.",
            ]
        ),
        filename="internal-transfer-blurry.webp",
        validation={"status": "pass"},
    )

    assert candidate is not None
    assert candidate["document"]["document_number"] == "MV-2026-0010"
    assert candidate["line_item_count"] == 3
    first, second, third = candidate["line_items"][:3]
    assert first["item_name"] == "S45C PIN"
    assert first["specification"] == "8X60"
    assert first["quantity"] == 200
    assert second["item_name"] == "환봉"
    assert second["document_item_code"] == "AL6061"
    assert second["specification"] == "10파이"
    assert second["quantity"] == 50
    assert third["item_name"] == "절삭유"
    assert third["specification"] == "4L"
    assert third["quantity"] == 6
    assert third["unit"] == "CAN"


def test_smoke_metadata_includes_structured_vl_candidate_without_line_item_promotion():
    metadata = build_docuparse_vl_candidate_metadata(
        {
            "sample": "samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf",
            "text_preview": VL_08_TEXT,
            "elapsed_ms": 95000,
            "provider_available_candidate": True,
            "provider_available_decision_reason": "manual_visual_check_passed",
            "validation": {"status": "pass", "matched_terms": ["QT-2026-0808-009", "473,000"]},
            "manual_visual_check": {
                "expected_from_pdf": {
                    "document_number": "QT-2026-0808-009",
                    "total_amount": "473,000",
                    "row_count": "2",
                }
            },
            "manual_visual_check_validation": {"severity": "pass", "issue_codes": []},
        }
    )

    candidate = metadata["vl_candidates"][0]
    assert candidate["candidate_only"] is True
    assert candidate["parser_integrated"] is False
    structured = candidate["structured_candidate"]
    assert structured["confirmed_promotion"] is False
    assert structured["document"]["document_number"] == "QT-2026-0808-009"
    assert structured["line_items"][0]["item_name"] == "고정 플레이트"
    assert "quantity" not in structured["line_items"][0]
    assert metadata["vl_candidate_summary"]["parser_evaluated"] is True
    assert metadata["vl_candidate_summary"]["parsed_line_item_count"] == 2
