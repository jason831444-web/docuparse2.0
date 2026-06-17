from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

from app.core.config import get_settings


class VLCandidateWorkerClient:
    provider = "paddleocr_vl_1_6_gguf"

    def __init__(self, worker_url: str | None = None, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self.worker_url = (worker_url or settings.paddleocr_vl_gguf_worker_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.paddleocr_vl_gguf_timeout_seconds

    def enabled(self) -> bool:
        settings = get_settings()
        return bool(
            settings.enable_paddleocr_vl_gguf
            and settings.paddleocr_vl_gguf_primary_reader_enabled
            and settings.paddleocr_vl_gguf_upload_pipeline_enabled
            and self.worker_url
        )

    def analyze(self, file_path: Path, *, original_filename: str = "") -> dict[str, Any]:
        if not self.enabled():
            return self._skipped("vl_worker_disabled_or_unconfigured")
        started = time.perf_counter()
        file_path = Path(file_path)
        if not file_path.exists():
            return self._failed(f"vl_worker_upload_file_not_found: {file_path}", started)
        try:
            upload_url = f"{self.worker_url}/analyze-upload"
            filename = original_filename or file_path.name
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            with file_path.open("rb") as handle:
                response = requests.post(
                    upload_url,
                    files={"file": (filename, handle, content_type)},
                    data={"original_filename": filename},
                    timeout=self.timeout_seconds,
                )
            if response.status_code in {404, 405}:
                return self._analyze_by_path(file_path, original_filename=original_filename, started=started)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return self._failed("vl_worker_invalid_json", started)
            payload.setdefault("provider", self.provider)
            payload.setdefault("elapsed_ms", int((time.perf_counter() - started) * 1000))
            payload.setdefault("worker_transport", "multipart_upload")
            return payload
        except Exception as exc:
            return self._failed(f"{type(exc).__name__}: {exc}", started)

    def _analyze_by_path(self, file_path: Path, *, original_filename: str = "", started: float) -> dict[str, Any]:
        try:
            response = requests.post(
                f"{self.worker_url}/analyze",
                json={
                    "file_path": str(file_path),
                    "original_filename": original_filename,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return self._failed("vl_worker_invalid_json", started)
            payload.setdefault("provider", self.provider)
            payload.setdefault("elapsed_ms", int((time.perf_counter() - started) * 1000))
            payload.setdefault("worker_transport", "shared_file_path")
            return payload
        except Exception as exc:
            return self._failed(f"{type(exc).__name__}: {exc}", started)

    def _skipped(self, reason: str) -> dict[str, Any]:
        return {
            "ok": False,
            "provider": self.provider,
            "status": "skipped",
            "fallback_reason": reason,
            "candidate_metadata": {"vl_candidates": [], "vl_candidate_summary": {"candidate_count": 0}},
        }

    def _failed(self, reason: str, started: float) -> dict[str, Any]:
        return {
            "ok": False,
            "provider": self.provider,
            "status": "failed",
            "fallback_reason": reason,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "candidate_metadata": {"vl_candidates": [], "vl_candidate_summary": {"candidate_count": 0}},
        }
