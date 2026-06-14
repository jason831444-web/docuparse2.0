from app.scripts.download_paddleocr_vl_onnx import EXPECTED_FILES, download_or_validate, inspect_bundle


def test_inspect_bundle_reports_missing_expected_files(tmp_path):
    target = tmp_path / "model"
    target.mkdir()
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")

    info = inspect_bundle(target)

    assert info["complete"] is False
    assert "onnx/decoder_model_merged.onnx" in info["missing_files"]


def test_download_or_validate_skips_complete_local_bundle(tmp_path):
    target = tmp_path / "model"
    for filename in EXPECTED_FILES:
        path = target / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub", encoding="utf-8")

    report = download_or_validate("example/model", target, local_files_only=True)

    assert report["summary"]["ok"] is True
    assert report["summary"]["download_skipped"] is True
    assert report["summary"]["missing_files"] == []


def test_download_or_validate_fails_local_only_when_bundle_incomplete(tmp_path):
    target = tmp_path / "model"
    target.mkdir()

    report = download_or_validate("example/model", target, local_files_only=True)

    assert report["summary"]["ok"] is False
    assert report["summary"]["download_error"] == "local_files_only_bundle_incomplete"
