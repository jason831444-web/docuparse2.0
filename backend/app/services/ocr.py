from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.core.config import get_settings

_paddleocr_runtime_disabled_reason: str | None = None


def configure_paddle_runtime_env() -> None:
    """Set conservative CPU runtime flags before importing/initializing Paddle."""
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_DISABLE_SIGNAL_HANDLER", "1")


@dataclass
class OCRProviderAttempt:
    provider: str
    succeeded: bool
    failed_reason: str | None = None


@dataclass
class OCRResult:
    text: str
    confidence: float
    engine_name: str
    provider_attempted: list[str] = field(default_factory=list)
    provider_succeeded: str | None = None
    provider_failed_reason: dict[str, str] = field(default_factory=dict)
    table_blocks: list[dict[str, Any]] = field(default_factory=list)
    line_candidates: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int | None = None
    ocr_worker_url_used: str | None = None
    ocr_worker_available: bool | None = None
    ocr_fallback_used: bool = False
    ocr_worker_metadata: dict[str, Any] = field(default_factory=dict)


class TesseractOCRProvider:
    engine_name = "tesseract"

    def extract(self, image_path: Path) -> OCRResult:
        processed = self._preprocess(image_path)
        text = pytesseract.image_to_string(processed)
        data = pytesseract.image_to_data(processed, output_type=pytesseract.Output.DICT)
        confidences = [
            float(value)
            for value in data.get("conf", [])
            if value not in ("-1", -1) and str(value).strip()
        ]
        avg_confidence = max(0.0, min(1.0, (sum(confidences) / len(confidences) / 100) if confidences else 0.0))
        return OCRResult(
            text=text.strip(),
            confidence=avg_confidence,
            engine_name=self.engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
        )

    def _preprocess(self, image_path: Path) -> Image.Image:
        image = cv2.imread(str(image_path))
        if image is None:
            return Image.open(image_path)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, h=10)
        threshold = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        return Image.fromarray(np.asarray(threshold))


class PaddleOCRProvider:
    engine_name = "paddleocr"

    def __init__(self) -> None:
        self._ocr: Any | None = None

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("paddleocr") is not None

    def extract(self, image_path: Path) -> OCRResult:
        global _paddleocr_runtime_disabled_reason
        if _paddleocr_runtime_disabled_reason:
            raise RuntimeError(f"paddleocr runtime disabled after previous failure: {_paddleocr_runtime_disabled_reason}")
        if not self.is_available():
            raise RuntimeError("paddleocr package is not installed")
        timeout = float(os.getenv("PADDLEOCR_TIMEOUT_SECONDS", "20"))
        command = [
            sys.executable,
            "-m",
            "app.services.paddleocr_worker",
            str(image_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"paddleocr subprocess timed out after {timeout:.0f}s") from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            message = f"paddleocr subprocess failed with code {completed.returncode}: {stderr[-800:]}"
            if completed.returncode < 0 or "Segmentation fault" in stderr or "SIGSEGV" in stderr:
                _paddleocr_runtime_disabled_reason = message
            raise RuntimeError(message)
        payload = self._parse_worker_payload(completed.stdout)
        text = str(payload.get("text") or "")
        confidence = _clamp_confidence(payload.get("confidence"))
        table_blocks = payload.get("table_blocks") if isinstance(payload.get("table_blocks"), list) else []
        line_candidates = payload.get("line_candidates") if isinstance(payload.get("line_candidates"), list) else []
        return OCRResult(
            text=text.strip(),
            confidence=confidence,
            engine_name=self.engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
            table_blocks=table_blocks,
            line_candidates=line_candidates,
        )

    def _parse_worker_payload(self, output: str) -> dict[str, Any]:
        for line in reversed((output or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise RuntimeError(f"paddleocr subprocess did not return JSON output: {(output or '')[-800:]}")

    def _load(self) -> Any:
        if self._ocr is not None:
            return self._ocr
        configure_paddle_runtime_env()
        from paddleocr import PaddleOCR

        lang = os.getenv("PADDLEOCR_LANG", "korean")
        ocr_version = os.getenv("PADDLEOCR_OCR_VERSION", "PP-OCRv4")

        # Prefer the PaddleOCR 2.x legacy API. PaddleOCR 3.x can route through
        # PaddleX/PIR even when PP-OCRv4 model names are requested, which is the
        # Linux CPU failure mode the isolated worker is designed to avoid.
        init_attempts = [
            {
                "lang": lang,
                "ocr_version": ocr_version,
                "use_angle_cls": False,
                "use_gpu": False,
                "enable_mkldnn": False,
                "show_log": False,
            },
            {
                "lang": lang,
                "ocr_version": ocr_version,
                "use_angle_cls": False,
                "use_gpu": False,
                "enable_mkldnn": False,
            },
            {
                "lang": lang,
                "ocr_version": ocr_version,
                "use_angle_cls": False,
                "use_gpu": False,
            },
            {
                "lang": lang,
                "use_angle_cls": False,
                "use_gpu": False,
            },
            {"use_angle_cls": False},
        ]
        errors: list[str] = []
        for kwargs in init_attempts:
            try:
                self._ocr = PaddleOCR(**kwargs)
                return self._ocr
            except Exception as exc:
                errors.append(f"{exc.__class__.__name__} for {sorted(kwargs)}: {exc}")
                continue
        raise RuntimeError("PaddleOCR initialization failed for all compatible argument sets: " + " | ".join(errors[-3:]))
        return self._ocr

    def _run_ocr(self, ocr: Any, image_path: Path) -> Any:
        if hasattr(ocr, "ocr"):
            try:
                return ocr.ocr(str(image_path), cls=False)
            except TypeError:
                return ocr.ocr(str(image_path))
        if hasattr(ocr, "predict"):
            return ocr.predict(str(image_path))
        raise RuntimeError("PaddleOCR object exposes neither legacy ocr(...) nor predict(...)")

    def _normalize_output(self, output: Any) -> tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]:
        lines: list[str] = []
        confidences: list[float] = []
        table_blocks: list[dict[str, Any]] = []
        line_candidates: list[dict[str, Any]] = []
        seen_candidates: set[tuple[str, tuple[tuple[float, float], ...] | None]] = set()
        for item in self._walk(output):
            if isinstance(item, dict):
                text = item.get("text") or item.get("rec_text") or item.get("label")
                score = item.get("score") or item.get("confidence") or item.get("rec_score")
                if isinstance(item.get("res"), dict):
                    table_blocks.append(item.get("res"))
                if text:
                    lines.append(str(text))
                    candidate = _ocr_line_candidate_from_mapping(item, str(text), score)
                    _append_unique_line_candidate(line_candidates, seen_candidates, candidate)
                if score is not None:
                    confidences.append(_clamp_confidence(score))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                maybe_text = item[1]
                if isinstance(maybe_text, (list, tuple)) and maybe_text and isinstance(maybe_text[0], str):
                    lines.append(str(maybe_text[0]))
                    candidate = _ocr_line_candidate(str(maybe_text[0]), maybe_text[1] if len(maybe_text) > 1 else None, item[0])
                    _append_unique_line_candidate(line_candidates, seen_candidates, candidate)
                    if len(maybe_text) > 1:
                        confidences.append(_clamp_confidence(maybe_text[1]))
        confidence = sum(confidences) / len(confidences) if confidences else (0.80 if lines else 0.0)
        return "\n".join(line for line in lines if line.strip()), confidence, table_blocks, line_candidates

    def _walk(self, value: Any):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from self._walk(nested)
        elif isinstance(value, (list, tuple)):
            yield value
            for nested in value:
                yield from self._walk(nested)


class OCRWorkerProvider:
    engine_name = "ocr_worker_paddleocr"

    def __init__(self, url: str | None = None, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self.url = (url or settings.ocr_worker_url or "").rstrip("/")
        self.timeout_seconds = float(timeout_seconds or settings.ocr_worker_timeout_seconds)

    def is_configured(self) -> bool:
        return bool(self.url)

    def extract(self, image_path: Path) -> OCRResult:
        if not self.url:
            raise RuntimeError("ocr_worker_unconfigured")
        payload = json.dumps({"image_path": str(image_path)}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/ocr",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise RuntimeError(f"ocr_worker_timeout after {self.timeout_seconds:.0f}s") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ocr_worker_unreachable: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"ocr_worker_failed: {exc}") from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ocr_worker_malformed_response: {body[-500:]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("ocr_worker_malformed_response: non-object payload")
        if not data.get("ok", True):
            error = data.get("error") or "unknown worker error"
            raise RuntimeError(f"ocr_worker_failed: {error}")
        text = str(data.get("text") or "")
        table_blocks = data.get("table_blocks") if isinstance(data.get("table_blocks"), list) else []
        line_candidates = data.get("line_candidates") if isinstance(data.get("line_candidates"), list) else []
        engine_name = str(data.get("engine_name") or self.engine_name)
        worker_metadata = {
            key: data.get(key)
            for key in (
                "retry_used",
                "provider_reset_used",
                "provider_reset_reason",
                "requests_since_provider_reset",
                "worker_attempt_count",
            )
            if key in data
        }
        return OCRResult(
            text=text.strip(),
            confidence=_clamp_confidence(data.get("confidence")),
            engine_name=engine_name,
            provider_attempted=[self.engine_name],
            provider_succeeded=self.engine_name,
            table_blocks=table_blocks,
            line_candidates=line_candidates,
            elapsed_ms=elapsed_ms,
            ocr_worker_url_used=self.url,
            ocr_worker_available=True,
            ocr_worker_metadata=worker_metadata,
        )


class OCRService:
    """Provider-routed OCR service with remote PaddleOCR worker and Tesseract fallback."""

    engine_name = "auto"

    def __init__(
        self,
        worker_provider: OCRWorkerProvider | None = None,
        paddle_provider: PaddleOCRProvider | None = None,
        tesseract_provider: TesseractOCRProvider | None = None,
        prefer_paddleocr: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.worker_provider = worker_provider or OCRWorkerProvider()
        self.paddle_provider = paddle_provider or PaddleOCRProvider()
        self.tesseract_provider = tesseract_provider or TesseractOCRProvider()
        self.prefer_ocr_worker = bool(settings.prefer_ocr_worker and self.worker_provider.is_configured())
        self.prefer_paddleocr = settings.local_paddleocr_enabled if prefer_paddleocr is None else prefer_paddleocr

    def extract(self, image_path: Path) -> OCRResult:
        attempts: list[str] = []
        failures: dict[str, str] = {}
        providers = []
        if self.prefer_ocr_worker:
            providers.append(self.worker_provider)
        if self.prefer_paddleocr:
            providers.append(self.paddle_provider)
        providers.append(self.tesseract_provider)
        for provider in providers:
            attempts.append(provider.engine_name)
            try:
                result = provider.extract(image_path)
                result.provider_attempted = attempts
                result.provider_failed_reason = failures
                result.ocr_fallback_used = bool(failures)
                self.engine_name = result.engine_name
                return result
            except Exception as exc:
                failures[provider.engine_name] = str(exc)
        return OCRResult(
            text="",
            confidence=0.0,
            engine_name="unavailable",
            provider_attempted=attempts,
            provider_succeeded=None,
            provider_failed_reason=failures,
            ocr_fallback_used=bool(failures),
        )

    def extract_text(self, image_path: Path) -> tuple[str, float]:
        result = self.extract(image_path)
        return result.text, result.confidence


def provider_health() -> dict[str, Any]:
    settings = get_settings()
    paddle_importable = PaddleOCRProvider.is_available()
    paddle_usable, paddle_error = _paddleocr_usable()
    paddle_vl_status = _paddleocr_vl_status()
    paddle_vl_importable = bool(paddle_vl_status.get("importable"))
    paddle_vl_usable = bool(paddle_vl_status.get("usable"))
    paddle_vl_error = paddle_vl_status.get("error")
    gguf_status = _paddleocr_vl_gguf_status()
    gguf_available = bool(gguf_status.get("available"))
    gguf_candidate_available = bool(gguf_status.get("candidate_available"))
    gguf_primary_reader_available = bool(gguf_status.get("primary_reader_available"))
    gguf_status_name = str(gguf_status.get("status") or "disabled")
    gguf_error = gguf_status.get("error")
    worker_health, worker_error = _ocr_worker_health(settings.ocr_worker_url, settings.ocr_worker_timeout_seconds)
    worker_model = (worker_health or {}).get("ocr_version") or "PP-OCRv4"
    primary_provider = settings.ai_primary_provider or "paddleocr_vl_1_6_gguf"
    primary_provider_available = bool(primary_provider == "paddleocr_vl_1_6_gguf" and gguf_available)
    if primary_provider == "paddleocr_vl":
        # The full runtime is intentionally retained only as a documented fallback
        # candidate because it exceeded the current 8GB CPU server budget.
        primary_provider_available = False
    active_ocr_engine = "PaddleOCR-VL GGUF" if primary_provider_available else str(worker_model)
    runtime_strategy = (
        "paddleocr_vl_1_6_gguf_candidate_with_ppocrv4_fallback"
        if primary_provider_available
        else "paddleocr_vl_1_6_gguf_primary_reader_with_ppocrv4_validation_fallback"
        if primary_provider == "paddleocr_vl_1_6_gguf" and gguf_primary_reader_available
        else "ppocrv4_fallback"
    )
    fallback_reason = None
    if not primary_provider_available:
        if primary_provider == "paddleocr_vl_1_6_gguf":
            fallback_reason = gguf_error or gguf_status_name
        elif primary_provider == "paddleocr_vl":
            fallback_reason = "paddleocr_vl_official_full_memory_blocked_on_8gb_cpu"
        else:
            fallback_reason = paddle_vl_error or "paddleocr_vl_unavailable"
    return {
        "ocr_engine": active_ocr_engine,
        "ocr_model": settings.paddleocr_vl_gguf_model_file if primary_provider_available else worker_model,
        "primary_provider": primary_provider,
        "primary_provider_enabled": bool(settings.enable_paddleocr_vl_gguf),
        "primary_provider_available": primary_provider_available,
        "primary_provider_candidate_available": bool(
            primary_provider == "paddleocr_vl_1_6_gguf" and gguf_candidate_available
        ),
        "primary_reader_available": bool(
            primary_provider == "paddleocr_vl_1_6_gguf" and gguf_primary_reader_available
        ),
        "primary_reader_mode": (
            "candidate_only_validated_by_parser"
            if primary_provider == "paddleocr_vl_1_6_gguf" and gguf_primary_reader_available
            else None
        ),
        "primary_provider_status": "active_candidate" if primary_provider_available else gguf_status_name,
        "fallback_provider": settings.ocr_fallback_provider,
        "fallback_provider_available": worker_health is not None or paddle_usable,
        "fallback_reason": fallback_reason,
        "runtime_strategy": runtime_strategy,
        "device": (worker_health or {}).get("device") or "cpu",
        "tesseract_available": _module_available("pytesseract"),
        "ocr_worker_configured": bool(settings.ocr_worker_url),
        "ocr_worker_url": settings.ocr_worker_url,
        "ocr_worker_reachable": worker_health is not None,
        "ocr_worker_health": worker_health,
        "ocr_worker_error": worker_error,
        "paddleocr_importable": paddle_importable,
        "paddleocr_importable_in_backend": paddle_importable,
        "paddleocr_usable": paddle_usable,
        "paddleocr_init_error": paddle_error,
        "paddleocr_runtime_mode": "ocr_worker" if settings.ocr_worker_url else ("subprocess_isolated" if paddle_importable else "unavailable"),
        "paddleocr_runtime_probe": "document_level_only",
        "paddleocr_runtime_note": "Actual inference is isolated per document and falls back to Tesseract on timeout or worker failure.",
        "paddleocr_runtime_disabled_reason": _paddleocr_runtime_disabled_reason,
        "paddleocr_vl_importable": paddle_vl_importable,
        "paddleocr_vl_usable": paddle_vl_usable,
        "paddleocr_vl_init_error": paddle_vl_error,
        "paddleocr_vl_runtime_mode": (
            "primary_provider"
            if primary_provider_available
            else ("primary_reader_candidate" if gguf_primary_reader_available else "candidate_not_integrated")
            if gguf_candidate_available
            else "unavailable"
        ),
        "paddleocr_vl_model": settings.paddleocr_vl_model_name,
        "paddleocr_vl_model_dir": str(settings.paddleocr_vl_model_dir) if settings.paddleocr_vl_model_dir else None,
        "paddleocr_vl_hf_repo": settings.paddleocr_vl_hf_repo,
        "paddleocr_vl_probe": paddle_vl_status,
        "paddleocr_vl_official_full": {
            "provider": "paddleocr_vl_official_full",
            "status": "memory_blocked_on_8gb_cpu",
            "server_smoke_summary": {
                "sample": "08_image_quote_missing_quantity.pdf",
                "readable_output": True,
                "elapsed": "about 4 minutes",
                "peak_rss": "about 5.5GiB",
                "swap_used": "about 3.7GiB-5.8GiB",
            },
        },
        "paddleocr_vl_gguf": gguf_status,
    }


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _ocr_worker_health(url: str | None, timeout_seconds: float) -> tuple[dict[str, Any] | None, str | None]:
    if not url:
        return None, "ocr worker is not configured"
    request = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=min(timeout_seconds, 5.0)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, str(exc)
    return payload if isinstance(payload, dict) else {"raw": payload}, None


def _paddleocr_usable() -> tuple[bool, str | None]:
    if not PaddleOCRProvider.is_available():
        return False, "paddleocr package is not installed"
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        return True, None
    except Exception as exc:
        return False, str(exc)


def _paddleocr_vl_status() -> dict[str, Any]:
    settings = get_settings()
    if not settings.enable_paddleocr_vl:
        return {"importable": False, "usable": False, "error": "paddleocr_vl_disabled"}
    probe = (
        "import importlib.util, json\n"
        "mods = {name: importlib.util.find_spec(name) is not None for name in ['paddleocr', 'paddlex', 'torch', 'transformers']}\n"
        "payload = {'modules': mods, 'importable': False, 'usable': False, 'error': None}\n"
        "try:\n"
        "    from paddleocr import PaddleOCRVL\n"
        "    payload['importable'] = True\n"
        "    payload['usable'] = all(mods.get(name) for name in ['paddleocr', 'paddlex'])\n"
        "except Exception as exc:\n"
        "    payload['error'] = str(exc)\n"
        "print(json.dumps(payload))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"importable": False, "usable": False, "error": f"paddleocr_vl_probe_failed: {exc}"}
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return {"importable": False, "usable": False, "error": message[-800:] or f"probe_exit_{completed.returncode}"}
    try:
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
    except Exception as exc:
        return {"importable": False, "usable": False, "error": f"paddleocr_vl_probe_malformed: {exc}"}
    payload.setdefault("importable", False)
    payload.setdefault("usable", bool(payload.get("importable")))
    payload.setdefault("error", None)
    return payload


def _paddleocr_vl_gguf_status() -> dict[str, Any]:
    settings = get_settings()
    model_dir = settings.paddleocr_vl_gguf_model_dir
    model_file = model_dir / settings.paddleocr_vl_gguf_model_file
    mmproj_file = model_dir / settings.paddleocr_vl_gguf_mmproj_file
    base: dict[str, Any] = {
        "provider": "paddleocr_vl_1_6_gguf",
        "repo_id": settings.paddleocr_vl_gguf_repo_id,
        "model_dir": str(model_dir),
        "model_file": settings.paddleocr_vl_gguf_model_file,
        "mmproj_file": settings.paddleocr_vl_gguf_mmproj_file,
        "server_url": settings.paddleocr_vl_gguf_server_url,
        "enabled": settings.enable_paddleocr_vl_gguf,
        "max_pages": settings.paddleocr_vl_gguf_max_pages,
        "concurrency": settings.paddleocr_vl_gguf_concurrency,
        "timeout_seconds": settings.paddleocr_vl_gguf_timeout_seconds,
        "smoke_passed": settings.paddleocr_vl_gguf_smoke_passed,
        "primary_reader_enabled": settings.paddleocr_vl_gguf_primary_reader_enabled,
        "primary_reader_available": False,
        "in_process_enabled": settings.paddleocr_vl_gguf_in_process_enabled,
        "candidate_available": False,
        "available": False,
        "error": None,
    }
    if not settings.enable_paddleocr_vl_gguf:
        base["status"] = "disabled"
        base["error"] = "paddleocr_vl_gguf_disabled"
        return base
    missing = [str(path) for path in (model_file, mmproj_file) if not path.exists()]
    if missing:
        base["status"] = "model_missing"
        base["missing_files"] = missing
        base["error"] = "paddleocr_vl_gguf_model_missing"
        return base
    health_url = settings.paddleocr_vl_gguf_server_url.rstrip("/")
    if health_url.endswith("/v1"):
        health_url = health_url[:-3]
    try:
        request = urllib.request.Request(f"{health_url}/health", method="GET")
        with urllib.request.urlopen(request, timeout=min(settings.paddleocr_vl_gguf_timeout_seconds, 5.0)) as response:
            health_payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        base["status"] = "llama_server_unreachable"
        base["error"] = f"llama_server_unreachable: {exc}"
        return base
    base["llama_server_health"] = health_payload if isinstance(health_payload, dict) else {"raw": health_payload}
    if not settings.paddleocr_vl_gguf_smoke_passed:
        base["status"] = "llama_server_ready"
        base["error"] = "paddleocr_vl_gguf_smoke_not_run"
        return base
    base["candidate_available"] = True
    if settings.paddleocr_vl_gguf_primary_reader_enabled:
        base["primary_reader_available"] = True
    if not settings.paddleocr_vl_gguf_in_process_enabled:
        if settings.paddleocr_vl_gguf_primary_reader_enabled:
            base["status"] = "primary_reader_candidate"
            base["error"] = "paddleocr_vl_gguf_confirmed_extraction_not_enabled"
        else:
            base["status"] = "active_candidate_not_integrated"
            base["error"] = "paddleocr_vl_gguf_in_process_disabled"
        return base
    base["status"] = "active_candidate"
    base["available"] = True
    return base


def _ocr_line_candidate_from_mapping(item: dict[str, Any], text: str, score: object) -> dict[str, Any] | None:
    bbox = (
        item.get("bbox")
        or item.get("box")
        or item.get("points")
        or item.get("poly")
        or item.get("polygon")
        or item.get("dt_poly")
        or item.get("dt_polys")
        or item.get("text_region")
    )
    return _ocr_line_candidate(text, score, bbox)


def _ocr_line_candidate(text: str, score: object, bbox: object) -> dict[str, Any] | None:
    normalized_bbox = _normalize_bbox_points(bbox)
    stripped = str(text or "").strip()
    if not stripped:
        return None
    candidate: dict[str, Any] = {
        "text": stripped,
        "confidence": _clamp_confidence(score),
    }
    if normalized_bbox:
        candidate["bbox"] = normalized_bbox
        xs = [point[0] for point in normalized_bbox]
        ys = [point[1] for point in normalized_bbox]
        candidate.update({
            "x_min": min(xs),
            "y_min": min(ys),
            "x_max": max(xs),
            "y_max": max(ys),
        })
    return candidate


def _normalize_bbox_points(value: object) -> list[list[float]] | None:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, dict):
        if all(key in value for key in ("x_min", "y_min", "x_max", "y_max")):
            try:
                x_min = float(value["x_min"])
                y_min = float(value["y_min"])
                x_max = float(value["x_max"])
                y_max = float(value["y_max"])
            except (TypeError, ValueError):
                return None
            return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
        for key in ("bbox", "box", "points", "poly", "polygon"):
            if key in value:
                return _normalize_bbox_points(value[key])
        return None
    if not isinstance(value, (list, tuple)):
        return None
    points: list[list[float]] = []
    for point in value:
        if isinstance(point, np.ndarray):
            point = point.tolist()
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            points.append([float(point[0]), float(point[1])])
        except (TypeError, ValueError):
            continue
    return points if len(points) >= 2 else None


def _append_unique_line_candidate(
    candidates: list[dict[str, Any]],
    seen: set[tuple[str, tuple[tuple[float, float], ...] | None]],
    candidate: dict[str, Any] | None,
) -> None:
    if not candidate:
        return
    bbox = candidate.get("bbox")
    bbox_key = tuple((round(float(point[0]), 2), round(float(point[1]), 2)) for point in bbox) if isinstance(bbox, list) else None
    key = (str(candidate.get("text") or ""), bbox_key)
    if key in seen:
        return
    seen.add(key)
    candidates.append(candidate)


def _clamp_confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))
