from app.scripts.smoke_paddleocr_vl_gguf import decide_provider_available_candidate


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
