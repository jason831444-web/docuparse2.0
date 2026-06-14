from app.services.vl_candidate_parser import VLCandidateParser
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata


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
