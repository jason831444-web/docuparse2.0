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


app = FastAPI(title="DocuParse OCR Worker")
logger = logging.getLogger(__name__)
configure_paddle_runtime_env()

_provider: PaddleOCRProvider | None = None
_provider_lock = threading.Lock()
_last_error: str | None = None


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


@app.get("/health")
def health() -> dict[str, Any]:
    importable = PaddleOCRProvider.is_available()
    return {
        "status": "ok" if importable else "unavailable",
        "paddleocr_importable": importable,
        "paddleocr_initialized": _provider is not None,
        "lazy_initialization": _provider is None,
        "model_cache_path": os.getenv("PADDLEOCR_CACHE_DIR") or str(Path.home() / ".paddlex"),
        "device": os.getenv("PADDLEOCR_DEVICE", "cpu"),
        "lang": os.getenv("PADDLEOCR_LANG", "korean"),
        "ocr_version": os.getenv("PADDLEOCR_OCR_VERSION", "PP-OCRv4"),
        "det_model": os.getenv("PADDLEOCR_DET_MODEL", "PP-OCRv4_mobile_det"),
        "rec_model": os.getenv("PADDLEOCR_REC_MODEL", "korean_PP-OCRv4_mobile_rec"),
        "runtime_flags": {
            "FLAGS_use_onednn": os.getenv("FLAGS_use_onednn"),
            "FLAGS_use_mkldnn": os.getenv("FLAGS_use_mkldnn"),
            "FLAGS_enable_pir_api": os.getenv("FLAGS_enable_pir_api"),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": os.getenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"),
            "PADDLE_DISABLE_SIGNAL_HANDLER": os.getenv("PADDLE_DISABLE_SIGNAL_HANDLER"),
        },
        "last_error": _last_error,
    }


@app.post("/ocr")
def ocr(payload: OCRRequest):
    global _last_error
    started = time.monotonic()
    image_path = Path(payload.image_path)
    if not image_path.exists():
        _last_error = f"image path does not exist: {image_path}"
        return JSONResponse(status_code=404, content={"ok": False, "error": _last_error})
    try:
        provider = _get_provider()
        raw = provider._run_ocr(provider._load(), image_path)
        text, confidence, table_blocks = provider._normalize_output(raw)
        _last_error = None
        return {
            "ok": True,
            "engine_name": "ocr_worker_paddleocr",
            "text": text,
            "confidence": confidence,
            "table_blocks": table_blocks,
            "line_candidates": [],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        _last_error = str(exc)
        logger.exception("OCR worker PaddleOCR inference failed for %s", image_path)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": _last_error[-800:],
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )
