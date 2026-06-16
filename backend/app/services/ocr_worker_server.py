from __future__ import annotations

import os
import threading
import time
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.ocr import PaddleOCRProvider, configure_paddle_runtime_env


app = FastAPI(title="Docparse OCR Worker")
logger = logging.getLogger(__name__)
configure_paddle_runtime_env()

_provider: PaddleOCRProvider | None = None
_provider_lock = threading.Lock()
_inference_lock = threading.Lock()
_last_error: str | None = None
_requests_since_provider_reset = 0

_RESETTABLE_PADDLE_ERRORS = (
    "Tensor holds no memory",
    "PreconditionNotMet",
    "holder_ should not be null",
    "elementwise_mul",
    "elementwise_add",
)


class OCRRequest(BaseModel):
    image_path: str


def _get_provider() -> PaddleOCRProvider:
    global _provider
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is None:
            provider = PaddleOCRProvider()
            provider._load()
            _provider = provider
    return _provider


def _reset_provider() -> None:
    global _provider, _requests_since_provider_reset
    with _provider_lock:
        _provider = None
        _requests_since_provider_reset = 0


def _reset_after_requests_limit() -> int:
    try:
        return max(0, int(os.getenv("OCR_WORKER_RESET_AFTER_REQUESTS", "20")))
    except ValueError:
        return 20


def _reset_provider_if_request_limit_reached() -> bool:
    limit = _reset_after_requests_limit()
    if limit <= 0 or _provider is None or _requests_since_provider_reset < limit:
        return False
    logger.info(
        "OCR worker resetting PaddleOCR provider before request after %s successful requests",
        _requests_since_provider_reset,
    )
    _reset_provider()
    return True


def _is_resettable_paddle_error(exc: Exception) -> bool:
    message = str(exc)
    return any(pattern in message for pattern in _RESETTABLE_PADDLE_ERRORS)


def _run_provider_ocr(provider: PaddleOCRProvider, image_path: Path) -> tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]:
    raw = provider._run_ocr(provider._load(), image_path)
    normalized = provider._normalize_output(raw)
    if len(normalized) == 3:
        text, confidence, table_blocks = normalized
        return text, confidence, table_blocks, []
    return normalized


@app.get("/health")
def health() -> dict[str, Any]:
    importable = PaddleOCRProvider.is_available()
    model = os.getenv("PADDLEOCR_OCR_VERSION", "PP-OCRv4")
    return {
        "status": "ok" if importable else "unavailable",
        "ocr_engine": model,
        "model": model,
        "primary_provider": "paddleocr_ppocrv4",
        "primary_provider_available": importable,
        "fallback_provider": "tesseract",
        "paddleocr_importable": importable,
        "paddleocr_initialized": _provider is not None,
        "lazy_initialization": _provider is None,
        "model_cache_path": os.getenv("PADDLEOCR_CACHE_DIR") or str(Path.home() / ".paddlex"),
        "device": os.getenv("PADDLEOCR_DEVICE", "cpu"),
        "lang": os.getenv("PADDLEOCR_LANG", "korean"),
        "ocr_version": model,
        "runtime_strategy": "paddleocr_2x_legacy_ocr_api",
        "runtime_flags": {
            "FLAGS_use_onednn": os.getenv("FLAGS_use_onednn"),
            "FLAGS_use_mkldnn": os.getenv("FLAGS_use_mkldnn"),
            "FLAGS_enable_pir_api": os.getenv("FLAGS_enable_pir_api"),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": os.getenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"),
            "PADDLE_DISABLE_SIGNAL_HANDLER": os.getenv("PADDLE_DISABLE_SIGNAL_HANDLER"),
        },
        "last_error": _last_error,
        "reset_after_requests": _reset_after_requests_limit(),
        "requests_since_provider_reset": _requests_since_provider_reset,
    }


@app.post("/ocr")
def ocr(payload: OCRRequest):
    global _last_error, _requests_since_provider_reset
    started = time.monotonic()
    image_path = Path(payload.image_path)
    if not image_path.exists():
        _last_error = f"image path does not exist: {image_path}"
        return JSONResponse(status_code=404, content={"ok": False, "error": _last_error})
    retry_used = False
    provider_reset_used = False
    provider_reset_reason: str | None = None
    try:
        with _inference_lock:
            if _reset_provider_if_request_limit_reached():
                provider_reset_used = True
                provider_reset_reason = "request_limit"
            try:
                provider = _get_provider()
                text, confidence, table_blocks, line_candidates = _run_provider_ocr(provider, image_path)
            except Exception as exc:
                if not _is_resettable_paddle_error(exc):
                    raise
                retry_used = True
                provider_reset_used = True
                provider_reset_reason = "runtime_error"
                logger.warning(
                    "OCR worker resetting PaddleOCR provider after runtime error for %s: %s",
                    image_path,
                    str(exc)[-500:],
                )
                _reset_provider()
                provider = _get_provider()
                text, confidence, table_blocks, line_candidates = _run_provider_ocr(provider, image_path)
            _requests_since_provider_reset += 1
        _last_error = None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "OCR worker request succeeded image_path=%s elapsed_ms=%s retry_used=%s provider_reset_used=%s provider_reset_reason=%s requests_since_reset=%s text_length=%s confidence=%.4f",
            image_path,
            elapsed_ms,
            retry_used,
            provider_reset_used,
            provider_reset_reason,
            _requests_since_provider_reset,
            len(text or ""),
            confidence,
        )
        return {
            "ok": True,
            "engine_name": "ocr_worker_paddleocr",
            "text": text,
            "confidence": confidence,
            "table_blocks": table_blocks,
            "line_candidates": line_candidates,
            "elapsed_ms": elapsed_ms,
            "retry_used": retry_used,
            "provider_reset_used": provider_reset_used,
            "provider_reset_reason": provider_reset_reason,
            "requests_since_provider_reset": _requests_since_provider_reset,
            "worker_attempt_count": 2 if retry_used else 1,
        }
    except Exception as exc:
        _last_error = str(exc)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "OCR worker request failed image_path=%s elapsed_ms=%s retry_used=%s provider_reset_used=%s",
            image_path,
            elapsed_ms,
            retry_used,
            provider_reset_used,
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": _last_error[-800:],
                "retry_used": retry_used,
                "provider_reset_used": provider_reset_used,
                "provider_reset_reason": provider_reset_reason,
                "elapsed_ms": elapsed_ms,
            },
        )
