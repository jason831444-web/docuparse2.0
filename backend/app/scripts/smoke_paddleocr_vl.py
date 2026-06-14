from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings


EXPECTED_TERMS = [
    "견적서",
    "QT-2026-0808-009",
    "고정",
    "플레이트",
    "스테인리스",
    "473,000",
]


def _configure_runtime_env() -> None:
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_DISABLE_SIGNAL_HANDLER", "1")


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


def _lines_from_text(value: str) -> list[str]:
    return [line for raw in value.splitlines() if (line := _normalize_line(raw))]


def _walk_strings(value: Any, fragments: list[str]) -> None:
    if isinstance(value, dict):
        for key in ["block_content", "rec_text", "text", "content"]:
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
        json_payload = _coerce_payload(getattr(item, "json", None))
        markdown_payload = _coerce_payload(getattr(item, "markdown", None))
        _walk_strings(json_payload, fragments)
        _walk_strings(markdown_payload, fragments)
    lines: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        for line in _lines_from_text(_strip_html(fragment)):
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
    ok = bool(stripped) and len(stripped) >= 20 and not repeated_char and replacement_ratio < 0.08
    if expected_terms:
        ok = ok and bool(matched_terms)
    if not stripped:
        status = "output_empty"
    elif repeated_char or replacement_ratio >= 0.08:
        status = "degenerate_generation"
    elif expected_terms and not matched_terms:
        status = "document_terms_missing"
    elif len(stripped) < 20:
        status = "output_too_short"
    else:
        status = "candidate_text_generated"
    return {
        "ok": ok,
        "status": status,
        "text_length": len(stripped),
        "matched_terms": matched_terms,
    }


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paddleocr_vl_smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "paddleocr_vl_smoke_report.md").write_text(_markdown(report), encoding="utf-8")


def run_smoke(sample: Path, output_dir: Path) -> dict[str, Any]:
    _configure_runtime_env()
    settings = get_settings()
    start = time.perf_counter()
    report: dict[str, Any] = {
        "sample": str(sample),
        "model": settings.paddleocr_vl_model_name,
        "hf_repo": settings.paddleocr_vl_hf_repo,
        "model_dir": str(settings.paddleocr_vl_model_dir) if settings.paddleocr_vl_model_dir else None,
        "device": settings.paddleocr_vl_device,
        "python": sys.version,
        "platform": platform.platform(),
        "runtime": "paddleocr.PaddleOCRVL",
        "ok": False,
        "error": None,
    }
    if not sample.exists():
        report["error"] = "sample_missing"
        report["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        write_report(output_dir, report)
        return report

    try:
        from paddleocr import PaddleOCRVL
    except Exception as exc:
        report["error"] = f"paddleocr_vl_import_failed: {exc}"
        report["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        write_report(output_dir, report)
        return report

    pipeline_version = "v1.6" if "1.6" in settings.paddleocr_vl_model_name else "v1.5"
    kwargs: dict[str, Any] = {
        "pipeline_version": pipeline_version,
        "device": settings.paddleocr_vl_device,
    }
    if settings.paddleocr_vl_engine:
        kwargs["engine"] = settings.paddleocr_vl_engine
    if settings.paddleocr_vl_model_dir:
        kwargs["vl_rec_model_dir"] = str(settings.paddleocr_vl_model_dir)
    if settings.paddleocr_vl_layout_model_dir:
        kwargs["layout_detection_model_dir"] = str(settings.paddleocr_vl_layout_model_dir)

    try:
        pipeline = PaddleOCRVL(**kwargs)
        output = pipeline.predict(str(sample))
    except Exception as exc:
        report["error"] = f"paddleocr_vl_inference_failed: {type(exc).__name__}: {exc!r}"
        report["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        write_report(output_dir, report)
        return report

    text = extract_text(output)
    validation = validate_output_text(text, EXPECTED_TERMS if "08_image_quote_missing_quantity" in sample.name else [])
    report.update(
        {
            "ok": bool(validation["ok"]),
            "pipeline_version": pipeline_version,
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
            "output_type": str(type(output)),
            "text_preview": text[:2000],
            "validation": validation,
        }
    )
    write_report(output_dir, report)
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PaddleOCR-VL Smoke Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Model: `{report.get('model')}`",
        f"- Runtime: `{report.get('runtime')}`",
        f"- Sample: `{report.get('sample')}`",
        f"- Elapsed ms: `{report.get('elapsed_ms')}`",
        f"- Error: `{report.get('error')}`",
        f"- Validation: `{(report.get('validation') or {}).get('status')}`",
        f"- Matched terms: `{', '.join((report.get('validation') or {}).get('matched_terms') or [])}`",
        "",
        "## Output Preview",
        "",
        "```text",
        str(report.get("text_preview") or "")[:2000],
        "```",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run official PaddleOCR-VL smoke inference on one sample.")
    parser.add_argument(
        "--sample",
        type=Path,
        default=Path("samples/pdf_samples/docuparse_image_based_pdf_samples_10/08_image_quote_missing_quantity.pdf"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_smoke"))
    args = parser.parse_args()

    report = run_smoke(args.sample, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
