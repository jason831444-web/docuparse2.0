from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import get_settings
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata, extract_text, validate_output_text


app = FastAPI(title="Docparse PaddleOCR-VL GGUF Worker")
logger = logging.getLogger(__name__)

_pipeline: Any | None = None
_pipeline_lock = threading.Lock()
_inference_lock = threading.Lock()
_last_error: str | None = None


class VLAnalyzeRequest(BaseModel):
    file_path: str
    original_filename: str | None = None


def _get_pipeline() -> Any:
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        settings = get_settings()
        from paddleocr import PaddleOCRVL

        _pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            device="cpu",
            vl_rec_backend="llama-cpp-server",
            vl_rec_server_url=settings.paddleocr_vl_gguf_server_url,
            vl_rec_api_model_name=settings.paddleocr_vl_gguf_model_file,
            vl_rec_max_concurrency=settings.paddleocr_vl_gguf_concurrency,
            use_queues=False,
        )
        return _pipeline


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    model_file = settings.paddleocr_vl_gguf_model_dir / settings.paddleocr_vl_gguf_model_file
    mmproj_file = settings.paddleocr_vl_gguf_model_dir / settings.paddleocr_vl_gguf_mmproj_file
    ready = bool(model_file.exists() and mmproj_file.exists())
    return {
        "status": "ok" if ready else "model_missing",
        "provider": "paddleocr_vl_1_6_gguf",
        "worker_api": "vl_worker_server",
        "model_file_exists": model_file.exists(),
        "mmproj_file_exists": mmproj_file.exists(),
        "llama_server_url": settings.paddleocr_vl_gguf_server_url,
        "pipeline_initialized": _pipeline is not None,
        "concurrency": settings.paddleocr_vl_gguf_concurrency,
        "max_pages": settings.paddleocr_vl_gguf_max_pages,
        "n_predict": getattr(settings, "paddleocr_vl_gguf_n_predict", 512),
        "last_error": _last_error,
    }


@app.post("/analyze")
def analyze(request: VLAnalyzeRequest) -> dict[str, Any]:
    global _last_error
    started = time.perf_counter()
    path = Path(request.file_path)
    report: dict[str, Any] = {
        "ok": False,
        "provider": "paddleocr_vl_1_6_gguf",
        "source": "vl_worker_api",
        "sample": str(path),
        "original_filename": request.original_filename,
        "provider_available_candidate": False,
        "provider_available_decision_reason": "worker_not_completed",
        "manual_visual_check": {
            "sample": str(path),
            "pdf_opened_and_visually_checked": False,
            "notes": "Upload pipeline candidate; manual visual check has not been performed.",
        },
    }
    try:
        if not path.exists():
            raise FileNotFoundError(f"file_path_not_found: {path}")
        image_path = _prepare_input_image(path)
        settings = get_settings()
        with _inference_lock:
            output = _get_pipeline().predict(
                str(image_path),
                max_new_tokens=getattr(settings, "paddleocr_vl_gguf_n_predict", 512),
            )
        text = extract_text(output)
        validation = validate_output_text(text, [])
        report.update(
            {
                "ok": bool(validation.get("ok")),
                "classification": validation.get("status"),
                "validation": validation,
                "render": {"image_path": str(image_path)},
                "text_preview": text[:5000],
                "provider_available_candidate": bool(validation.get("ok")),
                "provider_available_decision_reason": "vl_worker_output_readable" if validation.get("ok") else "vl_worker_output_invalid",
            }
        )
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        report.update(
            {
                "ok": False,
                "classification": "error",
                "error": _last_error,
                "fallback_reason": _last_error,
                "provider_available_decision_reason": "vl_worker_error",
            }
        )
        logger.exception("PaddleOCR-VL worker analyze failed for %s", path)
    finally:
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        report["candidate_metadata"] = build_docuparse_vl_candidate_metadata(report)
    return report


def _prepare_input_image(path: Path) -> Path:
    if path.suffix.casefold() == ".pdf":
        return _render_first_page(path)
    return path


def _render_first_page(path: Path) -> Path:
    import fitz

    settings = get_settings()
    output_dir = settings.upload_dir / "vl_rendered_pages"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{path.stem}-vl-page-1.png"
    with fitz.open(path) as document:
        page = document.load_page(0)
        # Wider render catches right-edge table columns that PDF viewers may crop.
        pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        pixmap.save(output_path)
    return output_path
