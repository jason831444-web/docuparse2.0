from app.services.vl_candidate_parser import VLCandidateParser
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata
from app.models.document import DocumentType
from app.services.parser import ParsedDocument
from decimal import Decimal


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
