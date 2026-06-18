from pathlib import Path

from app.core.config import Settings


def test_settings_ignore_removed_vl_provider_env_keys(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AI_PRIMARY_PROVIDER=paddleocr_vl_1_6_gguf",
                "PADDLEOCR_VL_ONNX_MODEL_PATH=/tmp/legacy-onnx",
                "PADDLEOCR_VL_ONNX_MODEL_NAME=legacy",
                "QWEN2_5_VL_MODEL_NAME=legacy-qwen",
                "QWEN2_5_VL_DEVICE=auto",
            ]
        )
    )

    settings = Settings(_env_file=env_file)

    assert settings.ai_primary_provider == "paddleocr_vl_1_6_gguf"
    assert settings.ocr_fallback_provider == "paddleocr_ppocrv4"


def test_settings_default_to_gguf_candidate_with_full_vl_disabled():
    settings = Settings(_env_file=None)

    assert settings.ai_primary_provider == "paddleocr_vl_1_6_gguf"
    assert settings.document_processing_concurrency == 3
    assert settings.enable_paddleocr_vl_gguf is True
    assert settings.paddleocr_vl_gguf_primary_reader_enabled is True
    assert settings.paddleocr_vl_gguf_upload_pipeline_enabled is True
    assert settings.paddleocr_vl_gguf_in_process_enabled is False
    assert settings.enable_paddleocr_vl is False


def test_document_processing_concurrency_can_be_configured(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("DOCUMENT_PROCESSING_CONCURRENCY=2\n")

    settings = Settings(_env_file=env_file)

    assert settings.document_processing_concurrency == 2
