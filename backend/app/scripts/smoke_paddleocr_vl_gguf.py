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


EXPECTED_TERMS_BY_SAMPLE = {
    "08_image_quote_missing_quantity.pdf": [
        "견적서",
        "QT-2026-0808-009",
        "고정",
        "플레이트",
        "스테인리스",
        "473,000",
    ],
    "16_real_commercial_invoice_exchange_rate.pdf": [
        "COMMERCIAL",
        "INV-US-2026-0916-EX",
        "Linear",
        "Cable",
        "PCB",
        "650",
    ],
    "21_photo_fax_po_misaligned_amounts.pdf": [
        "팩스",
        "FAX-PO-2026-0921",
        "베어링",
        "S45C",
        "418,000",
    ],
}


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


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return repr(value)[:300]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(key): _json_safe(nested, depth=depth + 1) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested, depth=depth + 1) for nested in value[:200]]
    return repr(value)[:1000]


def summarize_output(output: Any, *, max_items: int = 80) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, item in enumerate(output or []):
        if len(summary) >= max_items:
            break
        entry: dict[str, Any] = {"index": index, "type": type(item).__name__}
        for attr in ["json", "markdown", "html", "text"]:
            value = _coerce_payload(getattr(item, attr, None))
            if value is None:
                continue
            if isinstance(value, str):
                entry[attr] = value[:1000]
            else:
                entry[attr] = _json_safe(value)
        summary.append(entry)
    return summary


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


def _expected_terms_for_sample(sample: Path) -> list[str]:
    return EXPECTED_TERMS_BY_SAMPLE.get(sample.name, [])


def _load_manual_visual_check(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manual_visual_check_file_must_contain_json_object")
    data.setdefault("pdf_opened_and_visually_checked", False)
    data.setdefault("hallucinations_found", [])
    data.setdefault("dangerous_errors_found", [])
    return data


def _evaluate_manual_visual_check(text: str, manual_visual_check: dict[str, Any] | None) -> dict[str, Any] | None:
    if not manual_visual_check:
        return None
    expected = manual_visual_check.get("expected_from_pdf") or {}
    expected_values = {
        key: value
        for key, value in expected.items()
        if isinstance(value, str) and value.strip() and key not in {"notes"}
    }
    matched_expected_values = {
        key: value for key, value in expected_values.items() if value in text
    }
    dangerous_errors = list(manual_visual_check.get("dangerous_errors_found") or [])
    hallucinations = list(manual_visual_check.get("hallucinations_found") or [])
    required_values = [
        str(value)
        for value in manual_visual_check.get("required_vl_output_values") or []
        if str(value).strip()
    ]
    matched_required_values = [value for value in required_values if value in text]
    missing_required_values = [value for value in required_values if value not in text]
    severity = "pass"
    if dangerous_errors or hallucinations:
        severity = "fail"
    elif missing_required_values:
        severity = "warn"
    ok = (
        bool(manual_visual_check.get("pdf_opened_and_visually_checked"))
        and not dangerous_errors
        and not hallucinations
    )
    return {
        "ok": ok,
        "matched_expected_values": matched_expected_values,
        "missing_expected_values": {
            key: value for key, value in expected_values.items() if key not in matched_expected_values
        },
        "matched_required_values": matched_required_values,
        "missing_required_values": missing_required_values,
        "dangerous_error_count": len(dangerous_errors),
        "hallucination_count": len(hallucinations),
        "severity": severity,
    }


def _render_first_page(sample: Path, output_dir: Path, *, scale: float = 2.0) -> dict[str, Any]:
    import fitz

    output_dir.mkdir(parents=True, exist_ok=True)
    png = output_dir / "sample_page_1.png"
    doc = fitz.open(str(sample))
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pix.save(str(png))
    return {
        "page_count": doc.page_count,
        "image_path": str(png),
        "width": pix.width,
        "height": pix.height,
        "scale": scale,
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paddleocr_vl_gguf_smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
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
        "## Manual Visual Check",
        "",
        "```json",
        json.dumps(report.get("manual_visual_check") or {}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Output Preview",
        "",
        "```text",
        str(report.get("text_preview") or "")[:3000],
        "```",
    ]
    (output_dir / "paddleocr_vl_gguf_smoke_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_smoke(
    sample: Path,
    output_dir: Path,
    *,
    manual_visual_check: dict[str, Any] | None = None,
    expected_terms: list[str] | None = None,
    render_scale: float = 2.0,
) -> dict[str, Any]:
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
        "manual_visual_check": manual_visual_check
        or {
            "sample": str(sample),
            "pdf_opened_and_visually_checked": False,
            "notes": "No manual visual check file was provided.",
        },
    }
    try:
        if not sample.exists():
            raise FileNotFoundError(f"sample_missing: {sample}")
        if not model_file.exists() or not mmproj_file.exists():
            raise FileNotFoundError("gguf_model_missing")
        report["llama_server_health"] = _get_json(f"{server_base}/health")
        report["llama_server_models"] = _get_json(f"{server_base}/v1/models")
        render = _render_first_page(sample, output_dir, scale=render_scale)
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
        report["output_blocks_preview"] = summarize_output(output)
        text = extract_text(output)
        terms = expected_terms if expected_terms is not None else _expected_terms_for_sample(sample)
        validation = validate_output_text(text, terms)
        manual_validation = _evaluate_manual_visual_check(text, manual_visual_check)
        report.update(
            {
                "ok": bool(validation["ok"]),
                "classification": validation["status"],
                "validation": validation,
                "manual_visual_check_validation": manual_validation,
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
    parser.add_argument(
        "--manual-visual-check-file",
        type=Path,
        help="JSON object recording PDF values verified by looking at the rendered source PDF.",
    )
    parser.add_argument(
        "--expected-term",
        action="append",
        default=None,
        help="Expected text fragment for output validation. Repeat to override sample defaults.",
    )
    parser.add_argument(
        "--render-scale",
        type=float,
        default=2.0,
        help="PyMuPDF render scale for the first page image passed to PaddleOCR-VL.",
    )
    args = parser.parse_args()
    report = run_smoke(
        args.sample,
        args.output_dir,
        manual_visual_check=_load_manual_visual_check(args.manual_visual_check_file),
        expected_terms=args.expected_term,
        render_scale=args.render_scale,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
