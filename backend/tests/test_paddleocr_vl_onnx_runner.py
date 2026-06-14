from pathlib import Path

from app.scripts.smoke_paddleocr_vl_onnx import _validate_output_text
from app.services.paddleocr_vl_onnx_runner import (
    PaddleOCRVLOnnxRunner,
    PaddleOCRVLOnnxRunnerError,
    inspect_model_bundle,
)


def test_missing_model_path_is_reported_without_importing_runtime(tmp_path):
    missing = tmp_path / "missing-model"

    info = inspect_model_bundle(missing)

    assert info["path_exists"] is False
    assert info["usable"] is False
    assert "decoder" in info["missing"]


def test_runner_raises_model_path_missing_before_optional_dependencies(tmp_path):
    try:
        PaddleOCRVLOnnxRunner(tmp_path / "missing-model")
    except PaddleOCRVLOnnxRunnerError as exc:
        assert exc.reason == "model_path_missing"
    else:  # pragma: no cover
        raise AssertionError("Expected missing model path to raise")


def test_fixed_patch_grid_preserves_document_orientation():
    grid_h, grid_w = PaddleOCRVLOnnxRunner._select_fixed_patch_grid(target_patches=576, aspect_ratio=1.42)

    assert grid_h * grid_w == 576
    assert grid_h % 2 == 0
    assert grid_w % 2 == 0
    assert grid_h > grid_w


def test_smoke_output_validation_rejects_prompt_echo_and_degenerate_text():
    assert _validate_output_text("", prompt="OCR:") == "output_empty"
    assert _validate_output_text("Use null for missing values. Use null for missing values.", prompt="OCR:") == "prompt_echo"
    assert _validate_output_text("\ufffd" + "步" * 40, prompt="OCR:") == "degenerate_generation"
    assert _validate_output_text("견적서\nQT-2026-0808-009", prompt="OCR:") == "candidate_text_generated"
