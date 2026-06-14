import json
from pathlib import Path

from app.scripts.smoke_paddleocr_vl_gguf import (
    _evaluate_manual_visual_check,
    apply_cli_runtime_overrides,
    build_docuparse_vl_candidate_metadata,
    classify_smoke_exception,
    decide_provider_available_candidate,
    manual_visual_check_template_for_sample,
    write_manual_visual_check_template,
)
from app.core.config import get_settings


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


def test_gguf_manual_visual_check_template_requires_human_visual_confirmation():
    template = manual_visual_check_template_for_sample(Path("08_image_quote_missing_quantity.pdf"))

    assert template["pdf_opened_and_visually_checked"] is False
    assert template["expected_from_pdf"]["document_number"] == "QT-2026-0808-009"
    assert template["expected_from_pdf"]["total_amount"] == "473,000"
    assert template["structured_checks"]["blank_quantity_rows"][0]["row_contains"] == "고정 플레이트"

    validation = {"ok": True, "status": "official_gguf_smoke_success"}
    manual_validation = {"ok": False, "severity": "pass"}
    available, reason = decide_provider_available_candidate(validation, template, manual_validation)

    assert available is False
    assert reason == "manual_visual_check_failed"


def test_gguf_manual_visual_check_template_writer(tmp_path):
    output = tmp_path / "manual" / "21.json"

    write_manual_visual_check_template(Path("21_photo_fax_po_misaligned_amounts.pdf"), output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["pdf_opened_and_visually_checked"] is False
    assert data["expected_from_pdf"]["document_number"] == "FAX-PO-2026-0921"
    assert data["structured_checks"]["expected_document_total"] == "418,000"


def test_gguf_commercial_invoice_template_records_text_layer_render_gap():
    template = manual_visual_check_template_for_sample(Path("16_real_commercial_invoice_exchange_rate.pdf"))

    assert template["pdf_opened_and_visually_checked"] is False
    assert template["expected_from_pdf"]["document_number"] == "INV-US-2026-0916-EX"
    assert template["structured_checks"]["expected_line_amounts"] == ["450.00", "110.00", "90.00"]
    assert "text layer contains row Amount values" in template["known_input_limitations"][0]


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


def test_gguf_manual_check_records_known_input_limitations():
    text = "COMMERCIAL INVOICE\nINV-US-2026-0916-EX\nTotal USD 650.00"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "known_input_limitations": ["rendered image omits far-right Amount column"],
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert "vl_candidate_known_input_limitation" in result["issue_codes"]


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


def test_gguf_manual_check_flags_missing_core_expected_pdf_value():
    text = "견적서\n고정 플레이트\n총액:473,000"
    manual = {
        "pdf_opened_and_visually_checked": True,
        "expected_from_pdf": {
            "document_number": "QT-2026-0808-009",
            "row_count": "2",
            "notes": "row_count is context only and should not become a warning by itself",
        },
    }

    result = _evaluate_manual_visual_check(text, manual)

    assert result is not None
    assert result["severity"] == "warn"
    assert "vl_candidate_missing_expected_pdf_value" in result["issue_codes"]
    issue = next(issue for issue in result["issues"] if issue["code"] == "vl_candidate_missing_expected_pdf_value")
    assert issue["field"] == "document_number"
    assert issue["expected_value"] == "QT-2026-0808-009"
    assert not any(issue.get("field") == "row_count" for issue in result["issues"])


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


def test_gguf_smoke_classifies_runtime_and_model_failures_precisely():
    assert (
        classify_smoke_exception(ImportError("cannot import name 'PaddleOCRVL' from 'paddleocr'"))
        == "paddleocr_vl_runtime_missing_dependency"
    )
    assert classify_smoke_exception(FileNotFoundError("gguf_model_missing")) == "gguf_model_missing"
    assert classify_smoke_exception(FileNotFoundError("sample_missing: missing.pdf")) == "sample_missing"
    assert classify_smoke_exception(TimeoutError("timeout while waiting")) == "official_runtime_timeout"


def test_gguf_smoke_cli_runtime_overrides_clear_cached_settings(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("PADDLEOCR_VL_GGUF_MODEL_DIR", "/before")
    assert str(get_settings().paddleocr_vl_gguf_model_dir) == "/before"

    model_dir = tmp_path / "paddleocr_vl_1_6_gguf"
    overrides = apply_cli_runtime_overrides(
        model_dir=model_dir,
        model_file="custom-model.gguf",
        mmproj_file="custom-mmproj.gguf",
        server_url="http://127.0.0.1:8081/v1",
        concurrency=1,
    )

    assert overrides == {
        "PADDLEOCR_VL_GGUF_MODEL_DIR": str(model_dir),
        "PADDLEOCR_VL_GGUF_MODEL_FILE": "custom-model.gguf",
        "PADDLEOCR_VL_GGUF_MMPROJ_FILE": "custom-mmproj.gguf",
        "PADDLEOCR_VL_GGUF_SERVER_URL": "http://127.0.0.1:8081/v1",
        "PADDLEOCR_VL_GGUF_CONCURRENCY": "1",
    }
    settings = get_settings()
    assert settings.paddleocr_vl_gguf_model_dir == model_dir
    assert settings.paddleocr_vl_gguf_model_file == "custom-model.gguf"
    assert settings.paddleocr_vl_gguf_mmproj_file == "custom-mmproj.gguf"
    assert settings.paddleocr_vl_gguf_server_url == "http://127.0.0.1:8081/v1"
    assert settings.paddleocr_vl_gguf_concurrency == 1
