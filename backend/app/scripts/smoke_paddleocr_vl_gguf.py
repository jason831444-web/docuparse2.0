from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.core.config import get_settings


EXPECTED_TERMS = ["견적서", "QT-2026-0808-009", "고정", "플레이트", "스테인리스", "473,000"]


def _configure_runtime_env() -> None:
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_DISABLE_SIGNAL_HANDLER", "1")


def _server_base_url(server_url: str) -> str:
    base = server_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"raw": payload}


def _coerce_payload(payload: Any) -> Any:
    if callable(payload):
        try:
            return payload()
        except TypeError:
            return payload
    return payload


def _strip_html(value: str) -> str:
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value)


def _normalize_line(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return value.strip(" |")


def _walk_strings(value: Any, fragments: list[str]) -> None:
    if isinstance(value, dict):
        for key in ["block_content", "rec_text", "text", "content", "markdown", "html"]:
            content = value.get(key)
            if isinstance(content, str):
                fragments.append(content)
        for nested in value.values():
            _walk_strings(nested, fragments)
    elif isinstance(value, list):
        for nested in value:
            _walk_strings(nested, fragments)
    elif isinstance(value, str):
        fragments.append(value)


def extract_text(output: Any) -> str:
    fragments: list[str] = []
    for item in output or []:
        for attr in ["json", "markdown", "html", "text"]:
            _walk_strings(_coerce_payload(getattr(item, attr, None)), fragments)
        _walk_strings(item, fragments)
    lines: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        for raw in _strip_html(str(fragment)).splitlines():
            line = _normalize_line(raw)
            if not line:
                continue
            key = line.casefold()
            if key not in seen:
                seen.add(key)
                lines.append(line)
    return "\n".join(lines)


def validate_output_text(text: str, expected_terms: list[str] | None = None) -> dict[str, Any]:
    expected_terms = expected_terms or []
    stripped = text.strip()
    matched_terms = [term for term in expected_terms if term in stripped]
    repeated_char = bool(re.search(r"(.)\1{30,}", stripped))
    replacement_ratio = stripped.count("\ufffd") / max(len(stripped), 1)
    prompt_echo_only = stripped.lower() in {"ocr:", "read the document", "read the visible text"}
    ok = bool(stripped) and len(stripped) >= 20 and not repeated_char and replacement_ratio < 0.08 and not prompt_echo_only
    if expected_terms:
        ok = ok and bool(matched_terms)
    if not stripped:
        status = "output_empty"
    elif repeated_char or replacement_ratio >= 0.08:
        status = "degenerate_generation"
    elif expected_terms and not matched_terms:
        status = "document_terms_missing"
    elif prompt_echo_only:
        status = "prompt_echo_only"
    elif len(stripped) < 20:
        status = "output_too_short"
    else:
        status = "official_gguf_smoke_success" if ok else "candidate_text_generated"
    return {"ok": ok, "status": status, "text_length": len(stripped), "matched_terms": matched_terms}


def _render_first_page(sample: Path, output_dir: Path) -> dict[str, Any]:
    import fitz

    output_dir.mkdir(parents=True, exist_ok=True)
    png = output_dir / "sample_page_1.png"
    doc = fitz.open(str(sample))
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    pix.save(str(png))
    return {"page_count": doc.page_count, "image_path": str(png), "width": pix.width, "height": pix.height}


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paddleocr_vl_gguf_smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# PaddleOCR-VL GGUF Smoke Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Classification: `{report.get('classification')}`",
        f"- Server URL: `{report.get('server_url')}`",
        f"- Model: `{report.get('model')}`",
        f"- Sample: `{report.get('sample')}`",
        f"- Elapsed ms: `{report.get('elapsed_ms')}`",
        f"- Error: `{report.get('error')}`",
        f"- Matched terms: `{', '.join((report.get('validation') or {}).get('matched_terms') or [])}`",
        "",
        "## Output Preview",
        "",
        "```text",
        str(report.get("text_preview") or "")[:3000],
        "```",
    ]
    (output_dir / "paddleocr_vl_gguf_smoke_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_smoke(sample: Path, output_dir: Path) -> dict[str, Any]:
    _configure_runtime_env()
    settings = get_settings()
    started = time.perf_counter()
    model_dir = settings.paddleocr_vl_gguf_model_dir
    model_file = model_dir / settings.paddleocr_vl_gguf_model_file
    mmproj_file = model_dir / settings.paddleocr_vl_gguf_mmproj_file
    server_base = _server_base_url(settings.paddleocr_vl_gguf_server_url)
    report: dict[str, Any] = {
        "ok": False,
        "classification": None,
        "sample": str(sample),
        "server_url": settings.paddleocr_vl_gguf_server_url,
        "model": settings.paddleocr_vl_gguf_model_file,
        "mmproj": settings.paddleocr_vl_gguf_mmproj_file,
        "model_dir": str(model_dir),
        "python": sys.version,
        "platform": platform.platform(),
        "runtime": "paddleocr.PaddleOCRVL+llama-cpp-server",
        "error": None,
        "provider_available_candidate": False,
    }
    try:
        if not sample.exists():
            raise FileNotFoundError(f"sample_missing: {sample}")
        if not model_file.exists() or not mmproj_file.exists():
            raise FileNotFoundError("gguf_model_missing")
        report["llama_server_health"] = _get_json(f"{server_base}/health")
        report["llama_server_models"] = _get_json(f"{server_base}/v1/models")
        render = _render_first_page(sample, output_dir)
        report["render"] = render
        from paddleocr import PaddleOCRVL

        pipeline_started = time.perf_counter()
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            device="cpu",
            vl_rec_backend="llama-cpp-server",
            vl_rec_server_url=settings.paddleocr_vl_gguf_server_url,
            vl_rec_api_model_name=settings.paddleocr_vl_gguf_model_file,
            vl_rec_max_concurrency=settings.paddleocr_vl_gguf_concurrency,
            use_queues=False,
        )
        report["pipeline_created_ms"] = int((time.perf_counter() - pipeline_started) * 1000)
        predict_started = time.perf_counter()
        output = pipeline.predict(render["image_path"])
        report["predict_ms"] = int((time.perf_counter() - predict_started) * 1000)
        text = extract_text(output)
        validation = validate_output_text(text, EXPECTED_TERMS if "08_image_quote_missing_quantity" in sample.name else [])
        report.update(
            {
                "ok": bool(validation["ok"]),
                "classification": validation["status"],
                "validation": validation,
                "output_type": str(type(output)),
                "text_preview": text[:5000],
                "provider_available_candidate": bool(validation["ok"]),
            }
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc!r}"
        message = repr(exc).lower()
        if "missing" in message:
            report["classification"] = "model_or_sample_missing"
        elif "urlopen" in message or "connection" in message:
            report["classification"] = "llama_server_unreachable"
        elif "timeout" in message:
            report["classification"] = "official_runtime_timeout"
        else:
            report["classification"] = "paddleocr_vl_gguf_backend_error"
    finally:
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report(output_dir, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test PaddleOCR-VL-1.6 GGUF through llama-cpp-server.")
    parser.add_argument(
        "--sample",
        type=Path,
        default=Path("samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke"))
    args = parser.parse_args()
    report = run_smoke(args.sample, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
