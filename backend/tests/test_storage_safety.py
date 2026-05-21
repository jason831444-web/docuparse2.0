from pathlib import Path
from types import SimpleNamespace

from app.services import storage as storage_module


def _settings(upload_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        upload_dir=upload_dir,
        backend_base_url="http://testserver",
        max_upload_mb=12,
    )


def test_safe_original_filename_strips_path_and_unsafe_characters(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_module, "get_settings", lambda: _settings(tmp_path))
    service = storage_module.LocalStorageService()

    assert service.safe_original_filename("../Profile API | ✔.xlsx") == "Profile API _.xlsx"
    assert service.safe_original_filename("\x00..//") == "upload"


def test_delete_refuses_paths_outside_upload_root(monkeypatch, tmp_path):
    upload_root = tmp_path / "uploads"
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("do not delete", encoding="utf-8")
    inside_file = upload_root / "inside.txt"

    monkeypatch.setattr(storage_module, "get_settings", lambda: _settings(upload_root))
    service = storage_module.LocalStorageService()
    inside_file.write_text("delete me", encoding="utf-8")

    service.delete(str(outside_file))
    assert outside_file.exists()

    service.delete(str(inside_file))
    assert not inside_file.exists()
