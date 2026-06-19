from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.ai_document_understanding as ai_module
from app.models.document import DocumentType
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
        "llama_cpp_max_tokens": 700,
        "llama_cpp_temperature": 0.1,
        "ai_interpretation_max_chars": 12000,
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


def test_gemma_gguf_repair_provider_uses_text_only_candidate(monkeypatch, tmp_path):
    class FakeLlama:
        def __call__(self, prompt, **kwargs):
            assert "Do not OCR the image" in prompt
            assert "PO-2026-0001" in prompt
            return {
                "choices": [
                    {
                        "text": """
                        {
                          "document_type": "purchase_order",
                          "customer_name": "대성정공 구매팀",
                          "document_number": "PO-2026-0001",
                          "issue_date": "2026-06-16",
                          "currency": "KRW",
                          "extracted_amount": "999999",
                          "line_items": [
                            {"item_name": "S45C PIN", "specification": "8X60", "quantity": "120", "unit_price": "350", "line_total": "46200"}
                          ],
                          "confidence_score": "0.81",
                          "extraction_notes": ["parser 누락 필드를 보강했습니다."]
                        }
                        """
                    }
                ]
            }

    monkeypatch.setattr(ai_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(ai_module, "get_llama_cpp_gguf_model", lambda settings: FakeLlama())
    parsed = DocumentParser().parse(
        "발주서\n문서번호 PO-2026-0001\n발주일 2026.06.16\nNo 품명 규격 수량 단가 합계\n1 S45C PIN 8X60 120 350 46,200\n합계 95,150",
        "po.jpg",
    )

    result = ai_module.GemmaStructuredRepairDocumentAIService().analyze(
        tmp_path / "po.jpg",
        "발주서\n문서번호 PO-2026-0001\n발주일 2026.06.16",
        parsed,
        "po.jpg",
    )

    assert result.provider == "ai_repair_gemma_gguf"
    assert result.document_number == "PO-2026-0001"
    assert result.customer_name == "대성정공 구매팀"
    assert result.line_items[0]["quantity"] == 120
    assert result.extracted_amount == 999999
    assert result.review_required is True


def test_gemma_gguf_repair_provider_strips_amounts_for_no_price_delivery(monkeypatch, tmp_path):
    class FakeLlama:
        def __call__(self, prompt, **kwargs):
            return {
                "choices": [
                    {
                        "text": """
                        {
                          "document_type": "delivery_note",
                          "document_number": "DN-2026-0003",
                          "extracted_amount": "123000",
                          "subtotal": "120000",
                          "tax": "3000",
                          "line_items": [
                            {"item_name": "S45C PIN", "quantity": "500", "unit_price": "100", "line_total": "50000"}
                          ]
                        }
                        """
                    }
                ]
            }

    monkeypatch.setattr(ai_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(ai_module, "get_llama_cpp_gguf_model", lambda settings: FakeLlama())
    parsed = DocumentParser().parse(
        "납품서\n문서번호 DN-2026-0003\n단가 미기재 납품서 - 수량 검수용\nS45C PIN 500 EA",
        "delivery.jpg",
    )

    result = ai_module.GemmaStructuredRepairDocumentAIService().analyze(
        tmp_path / "delivery.jpg",
        "납품서\n단가 미기재 납품서 - 수량 검수용",
        parsed,
        "delivery.jpg",
    )

    assert result.document_type == DocumentType.delivery_note
    assert result.extracted_amount is None
    assert result.subtotal is None
    assert result.tax is None
    assert result.line_items[0]["item_name"] == "S45C PIN"
    assert result.line_items[0]["quantity"] == 500
    assert "unit_price" not in result.line_items[0]
    assert "line_total" not in result.line_items[0]
