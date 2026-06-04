from pathlib import Path
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

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


def _upload(filename: str, content: bytes, content_type: str = "application/octet-stream") -> UploadFile:
    file = SpooledTemporaryFile()
    file.write(content)
    file.seek(0)
    return UploadFile(filename=filename, file=file, headers={"content-type": content_type})


def test_upload_rejects_unsupported_extension(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_module, "get_settings", lambda: _settings(tmp_path))
    service = storage_module.LocalStorageService()

    with pytest.raises(ValueError, match="Unsupported file type"):
        service.save_upload(_upload("malware.exe", b"bad"))


def test_upload_rejects_file_larger_than_limit(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.max_upload_mb = 0
    monkeypatch.setattr(storage_module, "get_settings", lambda: settings)
    service = storage_module.LocalStorageService()

    with pytest.raises(ValueError, match="larger than"):
        service.save_upload(_upload("large.pdf", b"%PDF-1.4\nbody", "application/pdf"))


def test_duplicate_original_filenames_are_stored_as_separate_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_module, "get_settings", lambda: _settings(tmp_path))
    service = storage_module.LocalStorageService()

    first = service.save_upload(_upload("same-name.pdf", b"%PDF-1.4\none", "application/pdf"))
    second = service.save_upload(_upload("same-name.pdf", b"%PDF-1.4\ntwo", "application/pdf"))

    assert first != second
    assert first.exists()
    assert second.exists()
