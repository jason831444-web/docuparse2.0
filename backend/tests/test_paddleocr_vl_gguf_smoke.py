from app.scripts.smoke_paddleocr_vl_gguf import (
    _evaluate_manual_visual_check,
    build_docuparse_vl_candidate_metadata,
    decide_provider_available_candidate,
)


def test_gguf_provider_candidate_requires_manual_visual_pass():
    validation = {"ok": True, "status": "official_gguf_smoke_success"}
    manual_check = {"pdf_opened_and_visually_checked": True}
    manual_validation = {"ok": True, "severity": "pass"}

    available, reason = decide_provider_available_candidate(validation, manual_check, manual_validation)

    assert available is True
    assert reason == "manual_visual_check_passed"


def test_gguf_provider_candidate_rejects_missing_manual_check():
    validation = {"ok": True, "status": "official_gguf_smoke_success"}

    available, reason = decide_provider_available_candidate(validation, None, None)

    assert available is False
    assert reason == "manual_visual_check_missing"


def test_gguf_provider_candidate_rejects_manual_warn():
    validation = {"ok": True, "status": "official_gguf_smoke_success"}
    manual_check = {"pdf_opened_and_visually_checked": True}
    manual_validation = {"ok": True, "severity": "warn"}

    available, reason = decide_provider_available_candidate(validation, manual_check, manual_validation)

    assert available is False
    assert reason == "manual_visual_check_warn"


def test_gguf_provider_candidate_rejects_output_validation_failure():
    validation = {"ok": False, "status": "document_terms_missing"}
    manual_check = {"pdf_opened_and_visually_checked": True}
    manual_validation = {"ok": True, "severity": "pass"}

    available, reason = decide_provider_available_candidate(validation, manual_check, manual_validation)

    assert available is False
    assert reason == "document_terms_missing"


def test_gguf_manual_check_flags_blank_quantity_hallucination():
    text = "고정 플레이트 PLT-FIX-02 120x60x5T 8 EA 2800 280000 28000 308000"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "blank_quantity_rows": [
                {"row_contains": "고정 플레이트", "spec": "120x60x5T", "unit": "EA"}
            ]
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "fail"
    assert "vl_candidate_hallucinated_blank_quantity" in result["issue_codes"]


def test_gguf_manual_check_accepts_blank_quantity_when_unit_follows_spec():
    text = "고정 플레이트 PLT-FIX-02 120x60x5T EA 2800 280000 28000 308000"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "blank_quantity_rows": [
                {"row_contains": "고정 플레이트", "spec": "120x60x5T", "unit": "EA"}
            ]
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "pass"
    assert result["issue_codes"] == []


def test_gguf_manual_check_flags_missing_commercial_invoice_line_amounts():
    text = (
        "COMMERCIAL INVOICE\n"
        "INV-US-2026-0916-EX\n"
        "1 Linear Guide Rail HGW20 HGW20-1000 1000mm 10 EA 45.00\n"
        "Subtotal USD 650.00\n"
        "Total USD 650.00\n"
        "Exchange Rate USD = 1,370 KRW 참고"
    )
    manual = {
        "pdf_opened_and_visually_checked": True,
        "required_vl_output_values": ["INV-US-2026-0916-EX", "650.00"],
        "structured_checks": {
            "expected_line_amounts": ["450.00", "110.00", "90.00"],
            "exchange_rate_value": "1,370",
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert result["missing_required_values"] == []
    assert result["issue_codes"].count("vl_candidate_missing_line_amount") == 3


def test_gguf_manual_check_flags_exchange_rate_as_total_amount():
    text = "COMMERCIAL INVOICE\nTotal USD 1,370\nExchange Rate USD = 1,370 KRW 참고"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "exchange_rate_value": "1,370",
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "fail"
    assert "vl_candidate_exchange_rate_as_amount" in result["issue_codes"]


def test_gguf_manual_check_flags_missing_document_total_for_fax_candidate():
    text = "FAX-PO-2026-0921\n1 베어링 하우징 100mm 20 EA 8,000 160,000 16,000 176,000"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "expected_document_total": "418,000",
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert "vl_candidate_missing_document_total" in result["issue_codes"]


def test_gguf_manual_check_flags_missing_visually_verified_row_anchor():
    text = "FAX-PO-2026-0921\n1 베어링 하우징 100mm 20 EA 8,000 160,000 16,000 176,000"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "expected_row_anchors": [
                {"text": "S45C PIN 8X60", "label": "row 2 item"},
                {"text": "M8 볼트", "label": "row 3 item"},
            ],
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert result["issue_codes"].count("vl_candidate_missing_row_anchor") == 2


def test_gguf_manual_check_flags_degraded_row_fragment_and_cell():
    text = (
        "FAX-PO-2026-0921\n"
        "3 M8 볼트/와서 SEM8 1,000 1,000 SET 160 160,000 16,000 176,000"
    )
    manual = {
        "pdf_opened_and_visually_checked": True,
        "structured_checks": {
            "expected_row_fragments": [
                {"text": "M8 볼트 / 와셔 SET", "label": "row 3 item name"}
            ],
            "expected_row_cells": [
                {"row_contains": "M8", "cells": ["와셔", "M8", "SET", "176,000"]}
            ],
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert "vl_candidate_missing_row_fragment" in result["issue_codes"]
    assert "vl_candidate_missing_row_cell" in result["issue_codes"]


def test_gguf_smoke_report_builds_candidate_only_docuparse_metadata():
    report = {
        "provider_available_candidate": False,
        "provider_available_decision_reason": "manual_visual_check_warn",
        "validation": {"matched_terms": ["FAX-PO-2026-0921", "베어링"]},
        "manual_visual_check_validation": {
            "severity": "warn",
            "issue_codes": ["vl_candidate_missing_document_total"],
        },
        "text_preview": "FAX-PO-2026-0921\n1 베어링 하우징 ...",
        "elapsed_ms": 137716,
    }

    metadata = build_docuparse_vl_candidate_metadata(report)

    assert metadata["vl_candidate_summary"]["candidate_count"] == 1
    assert metadata["vl_candidate_summary"]["warning_count"] == 1
    assert metadata["vl_candidate_summary"]["provider_available_candidate"] is False
    assert metadata["vl_candidates"][0]["candidate_only"] is True
    assert metadata["vl_candidates"][0]["parser_integrated"] is False
    assert metadata["vl_candidates"][0]["review_flags"] == ["vl_candidate_missing_document_total"]


def test_gguf_candidate_metadata_deduplicates_issue_codes_but_keeps_details():
    report = {
        "provider_available_candidate": False,
        "provider_available_decision_reason": "manual_visual_check_warn",
        "validation": {"matched_terms": ["INV-US-2026-0916-EX"]},
        "manual_visual_check_validation": {
            "severity": "warn",
            "issue_codes": [
                "vl_candidate_missing_line_amount",
                "vl_candidate_missing_line_amount",
                "vl_candidate_missing_line_amount",
            ],
            "issues": [
                {"code": "vl_candidate_missing_line_amount", "expected_value": "450.00"},
                {"code": "vl_candidate_missing_line_amount", "expected_value": "110.00"},
                {"code": "vl_candidate_missing_line_amount", "expected_value": "90.00"},
            ],
        },
        "text_preview": "COMMERCIAL INVOICE ...",
        "elapsed_ms": 176350,
    }

    metadata = build_docuparse_vl_candidate_metadata(report)

    assert metadata["vl_candidate_summary"]["issue_codes"] == ["vl_candidate_missing_line_amount"]
    assert metadata["vl_candidates"][0]["review_flags"] == ["vl_candidate_missing_line_amount"]
    assert [issue["expected_value"] for issue in metadata["vl_candidates"][0]["issue_details"]] == [
        "450.00",
        "110.00",
        "90.00",
    ]
