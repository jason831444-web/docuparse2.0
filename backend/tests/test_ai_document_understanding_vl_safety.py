from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.ai_document_understanding as ai_module
from app.services.parser import DocumentParser


def _settings(**overrides):
    values = {
        "ai_primary_provider": "paddleocr_vl_1_6_gguf",
        "ai_secondary_provider": "heuristic_fallback",
        "ai_enable_second_pass": False,
        "ai_second_pass_confidence_threshold": 0.8,
        "enable_paddleocr_vl_gguf": True,
        "paddleocr_vl_gguf_in_process_enabled": False,
        "enable_paddleocr_vl": False,
        "paddleocr_vl_engine": None,
        "paddleocr_vl_model_dir": None,
        "paddleocr_vl_layout_model_dir": None,
        "paddleocr_vl_model_name": "PaddleOCR-VL-1.6",
        "paddleocr_vl_device": "cpu",
        "paddleocr_vl_gguf_server_url": "http://vl-worker-gguf:8080/v1",
        "paddleocr_vl_gguf_model_file": "PaddleOCR-VL-1.6-GGUF.gguf",
        "paddleocr_vl_gguf_concurrency": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_gguf_document_ai_service_blocks_in_process_import_by_default(monkeypatch):
    monkeypatch.setattr(ai_module, "get_settings", lambda: _settings())

    with pytest.raises(RuntimeError) as excinfo:
        ai_module.PaddleOCRVLDocumentAIService()

    message = str(excinfo.value)
    assert "in-process provider is disabled" in message
    assert "PP-OCRv4 fallback" in message


def test_hybrid_service_falls_back_when_gguf_in_process_provider_is_blocked(monkeypatch, tmp_path):
    ai_module.get_local_document_ai_service.cache_clear()
    ai_module.get_paddleocr_vl_document_ai_service.cache_clear()
    monkeypatch.setattr(ai_module, "get_settings", lambda: _settings())
    parsed = DocumentParser().parse("견적서\n견적번호 QT-2026-0808-009\n총액 473,000", "sample.pdf")

    service = ai_module.HybridOpenSourceDocumentAIService()
    result = service.analyze(tmp_path / "sample.pdf", "견적서\n총액 473,000", parsed, "sample.pdf")

    assert result.provider == "heuristic_fallback"
    assert result.provider_chain == ["paddleocr_vl_1_6_gguf_unavailable", "heuristic_fallback"]
    assert any("in-process provider is disabled" in note for note in result.extraction_notes)


def test_legacy_full_vl_remains_disabled_without_import(monkeypatch):
    monkeypatch.setattr(
        ai_module,
        "get_settings",
        lambda: _settings(
            ai_primary_provider="paddleocr_vl",
            enable_paddleocr_vl=False,
            enable_paddleocr_vl_gguf=False,
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        ai_module.PaddleOCRVLDocumentAIService()

    assert str(excinfo.value) == "PaddleOCR-VL provider is disabled by ENABLE_PADDLEOCR_VL=false."
