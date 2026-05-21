import shutil
import uuid
import mimetypes
import re
from typing import Protocol
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.services.file_type_detection import FileTypeDetector


ALLOWED_EXTENSIONS = FileTypeDetector().allowed_extensions()


class StorageService(Protocol):
    def save_upload(self, file: UploadFile) -> Path:
        ...

    def delete(self, stored_path: str | None) -> None:
        ...

    def public_url(self, stored_path: str) -> str:
        ...

    def safe_original_filename(self, filename: str | None) -> str:
        ...


class LocalStorageService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.root = self.settings.upload_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.detector = FileTypeDetector()

    def validate_upload(self, file: UploadFile) -> None:
        extension = Path(file.filename or "").suffix.lower().lstrip(".")
        if extension not in ALLOWED_EXTENSIONS:
            supported = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise ValueError(f"Unsupported file type. Supported formats: {supported}.")

    def save_upload(self, file: UploadFile) -> Path:
        self.validate_upload(file)
        suffix = Path(file.filename or "").suffix.lower()
        if not suffix:
            suffix = mimetypes.guess_extension(file.content_type or "") or ".bin"
        destination = self.root / f"{uuid.uuid4()}{suffix}"
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        if destination.stat().st_size > max_bytes:
            destination.unlink(missing_ok=True)
            raise ValueError(f"File is larger than {self.settings.max_upload_mb} MB.")
        return destination

    def delete(self, stored_path: str | None) -> None:
        if not stored_path:
            return
        path = Path(stored_path).resolve()
        if not self._within_upload_root(path):
            return
        path.unlink(missing_ok=True)

    def public_url(self, stored_path: str) -> str:
        return f"{self.settings.backend_base_url}/uploads/{Path(stored_path).name}"

    def safe_original_filename(self, filename: str | None) -> str:
        name = Path(filename or "upload").name
        name = name.replace("\x00", "")
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
        name = re.sub(r"(?:_\s*){2,}", "_", name)
        name = re.sub(r"\s+", " ", name).strip(" ._-")
        return name[:180] or "upload"

    def _within_upload_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False


class ObjectStorageService:
    """Deployment scaffold for S3/GCS/Azure style storage.

    The app remains local-first; this class makes the boundary explicit for a
    future object-storage implementation without hard-binding development to a
    cloud provider.
    """

    def __init__(self) -> None:
        raise NotImplementedError("Object storage is not configured. Use STORAGE_BACKEND=local for development.")


def get_storage_service() -> StorageService:
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorageService()
    if settings.storage_backend in {"object", "s3", "gcs", "azure"}:
        return ObjectStorageService()
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
