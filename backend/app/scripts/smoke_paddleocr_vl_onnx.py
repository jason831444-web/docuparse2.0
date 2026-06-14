from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, snapshot_download

from app.core.config import get_settings
from app.services.paddleocr_vl_onnx_runner import validate_generated_text


DEFAULT_REPO_ID = "lbm364dl/PaddleOCR-VL-1.5-ONNX"
DEFAULT_REQUIRED_FILES = [
    "tokenizer.json",
    "tokenizer.model",
    "processor_config.json",
    "preprocessor_config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-check a PaddleOCR-VL ONNX quantized model bundle without disabling PP-OCRv4 fallback."
    )
    parser.add_argument("--repo-id", default=os.getenv("PADDLEOCR_VL_ONNX_REPO_ID", DEFAULT_REPO_ID))
    parser.add_argument("--model-path", type=Path, default=_default_model_path())
    parser.add_argument("--runner-module", default=os.getenv("PADDLEOCR_VL_ONNX_RUNNER_MODULE", ""))
    parser.add_argument(
        "--run-inference",
        action="store_true",
        help="Run the built-in minimal ONNX runner against --sample inputs when dependencies are available.",
    )
    parser.add_argument("--sample", action="append", type=Path, default=[])
    parser.add_argument("--download", action="store_true", help="Download the selected HF repo to --model-path.")
    parser.add_argument("--check-sessions", action="store_true", help="Try loading each ONNX file in a child process.")
    parser.add_argument(
        "--subprocess-session-check",
        action="store_true",
        help="Alias for --check-sessions. Kept explicit for Linux/runtime compatibility smoke runs.",
    )
    parser.add_argument(
        "--onnxruntime-version-report",
        action="store_true",
        help="Include ONNX Runtime version/provider details in runtime and session reports.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("PADDLEOCR_VL_ONNX_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-pages", type=int, default=int(os.getenv("PADDLEOCR_VL_ONNX_MAX_PAGES", "1")))
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--prompt", default=os.getenv("PADDLEOCR_VL_ONNX_PROMPT", "OCR:"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/vl_onnx_smoke"))
    parser.add_argument(
        "--write-validation-marker",
        action="store_true",
        help="Write a small marker into the model directory only when a sample produces validated candidate text.",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    if args.subprocess_session_check:
        args.check_sessions = True
    if args.run_inference and not args.runner_module:
        args.runner_module = "app.services.paddleocr_vl_onnx_runner"
    report = smoke(args)
    report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    if args.write_validation_marker:
        _write_validation_marker(report, args.model_path)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "vl_onnx_smoke_report.json"
    md_path = args.output_dir / "vl_onnx_smoke_report.md"
    session_json_path = args.output_dir / "vl_onnx_session_report.json"
    session_md_path = args.output_dir / "vl_onnx_session_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    session_json_path.write_text(json.dumps(_session_only_report(report), ensure_ascii=False, indent=2), encoding="utf-8")
    session_md_path.write_text(_session_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {session_json_path}")
    print(f"Wrote {session_md_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


def _write_validation_marker(report: dict[str, Any], model_path: Path) -> None:
    successful = [
        sample
        for sample in report.get("samples", [])
        if sample.get("provider_used") == "paddleocr_vl_onnx_quantized"
        and sample.get("output_validation_status") == "candidate_text_generated"
        and not sample.get("fallback_used")
    ]
    if not successful:
        return
    marker = {
        "provider_used": "paddleocr_vl_onnx_quantized",
        "output_validation_status": "candidate_text_generated",
        "validated_at_unix": int(time.time()),
        "sample_count": len(successful),
        "onnxruntime_version": report.get("runtime", {}).get("onnxruntime_version"),
        "model_path": str(model_path),
    }
    marker_path = model_path / ".docuparse_vl_onnx_validated.json"
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")


def smoke(args: argparse.Namespace) -> dict[str, Any]:
    repo = _repo_info(args.repo_id)
    model_path = args.model_path
    download_result: dict[str, Any] | None = None
    if args.download:
        download_result = _download_repo(args.repo_id, model_path)

    bundle = _bundle_info(model_path)
    runtime = _runtime_info(args.runner_module)
    session_load = _session_load_report(model_path) if args.check_sessions else []
    samples = []
    can_infer = bool(bundle["usable"] and runtime["onnxruntime_available"] and runtime["runner_available"])
    for sample in args.sample:
        samples.append(
            _run_sample(
                sample,
                model_path=model_path,
                runner_module=args.runner_module,
                timeout_seconds=args.timeout_seconds,
                max_pages=args.max_pages,
                max_new_tokens=args.max_new_tokens,
                prompt=args.prompt,
                can_infer=can_infer,
            )
        )

    blocked_reasons = []
    if not bundle["path_exists"]:
        blocked_reasons.append("model_path_missing")
    if not bundle["onnx_files"]:
        blocked_reasons.append("onnx_model_missing")
    if bundle["missing_required_files"]:
        blocked_reasons.append("processor_or_tokenizer_missing")
    if not runtime["onnxruntime_available"]:
        blocked_reasons.append("onnxruntime_missing")
    if not runtime["runner_module"]:
        blocked_reasons.append("runner_module_missing")
    elif not runtime["runner_available"]:
        blocked_reasons.append("runner_module_unavailable")

    successful_samples = [sample for sample in samples if sample.get("provider_used") == "paddleocr_vl_onnx_quantized" and not sample.get("fallback_used")]
    sample_failure_reasons = sorted({str(sample.get("fallback_reason")) for sample in samples if sample.get("fallback_reason")})
    status = "ready" if successful_samples else "blocked"
    fallback_required = not successful_samples
    fallback_reason = ",".join(blocked_reasons) if blocked_reasons else None
    if fallback_required and sample_failure_reasons:
        fallback_reason = ",".join(sample_failure_reasons)
    if fallback_required and not fallback_reason and can_infer:
        fallback_reason = "paddleocr_vl_onnx_inference_not_validated"
    return {
        "summary": {
            "status": status,
            "repo_id": args.repo_id,
            "model_path": str(model_path),
            "license": repo.get("license"),
            "commercial_saas_use": "allowed_by_apache_2_0" if repo.get("license") == "apache-2.0" else "review_required",
            "model_loaded": bool(successful_samples),
            "provider_used": "paddleocr_vl_onnx_quantized" if successful_samples else None,
            "fallback_required": fallback_required,
            "fallback_provider": "paddleocr_ppocrv4",
            "fallback_reason": fallback_reason,
            "sample_count": len(samples),
            "successful_sample_count": len(successful_samples),
        },
        "repo": repo,
        "download": download_result,
        "environment": _environment_info(),
        "bundle": bundle,
        "runtime": runtime,
        "onnx_session_load": session_load,
        "diagnostic_interpretation": _diagnostic_interpretation(
            bundle,
            runtime,
            session_load,
            can_infer=can_infer,
            sample_validated=bool(successful_samples),
        ),
        "samples": samples,
        "download_commands": _download_commands(args.repo_id, model_path),
    }


def _default_model_path() -> Path:
    settings = get_settings()
    return settings.paddleocr_vl_onnx_model_path or (settings.ai_model_dir / "paddleocr_vl_onnx_quantized")


def _repo_info(repo_id: str) -> dict[str, Any]:
    try:
        api = HfApi()
        info = api.model_info(repo_id, files_metadata=True)
        siblings = sorted(info.siblings, key=lambda item: item.rfilename)
        total_size = sum(item.size or 0 for item in siblings)
        interesting = []
        for item in siblings:
            name = item.rfilename
            lower = name.lower()
            if any(token in lower for token in ["onnx", "tokenizer", "processor", "preprocessor", "config", "license", "readme"]):
                interesting.append({"path": name, "size": item.size})
        card_data = getattr(info, "card_data", None)
        license_value = card_data.get("license") if card_data else None
        return {
            "repo_id": repo_id,
            "license": license_value,
            "total_size_bytes": total_size,
            "total_size_gb": round(total_size / 1024**3, 3),
            "files": interesting,
            "error": None,
        }
    except Exception as exc:
        return {
            "repo_id": repo_id,
            "license": None,
            "total_size_bytes": None,
            "total_size_gb": None,
            "files": [],
            "error": str(exc),
        }


def _download_repo(repo_id: str, model_path: Path) -> dict[str, Any]:
    try:
        model_path.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=repo_id, local_dir=model_path, local_dir_use_symlinks=False)
        return {"ok": True, "path": str(model_path)}
    except Exception as exc:
        return {"ok": False, "path": str(model_path), "error": str(exc)}


def _bundle_info(model_path: Path) -> dict[str, Any]:
    path_exists = model_path.exists()
    onnx_files = sorted(str(path.relative_to(model_path)) for path in model_path.rglob("*.onnx")) if path_exists else []
    missing = [name for name in DEFAULT_REQUIRED_FILES if not (model_path / name).exists()]
    files_preview = []
    if path_exists:
        for path in sorted(item for item in model_path.rglob("*") if item.is_file())[:50]:
            files_preview.append({"path": str(path.relative_to(model_path)), "size": path.stat().st_size})
    return {
        "path": str(model_path),
        "path_exists": path_exists,
        "onnx_files": onnx_files[:20],
        "onnx_file_count": len(onnx_files),
        "missing_required_files": missing,
        "files_preview": files_preview,
        "usable": bool(path_exists and onnx_files and not missing),
    }


def _runtime_info(runner_module: str) -> dict[str, Any]:
    onnxruntime_available = importlib.util.find_spec("onnxruntime") is not None
    onnxruntime_version = None
    onnxruntime_providers: list[str] = []
    if onnxruntime_available:
        try:
            ort = importlib.import_module("onnxruntime")
            onnxruntime_version = getattr(ort, "__version__", None)
            onnxruntime_providers = list(ort.get_available_providers())
        except Exception:
            onnxruntime_providers = []
    runner_available = False
    runner_error = None
    if runner_module:
        try:
            module = importlib.import_module(runner_module)
            runner_available = hasattr(module, "predict")
            if not runner_available:
                runner_error = "predict_function_missing"
        except Exception as exc:
            runner_error = str(exc)
    return {
        "onnxruntime_available": onnxruntime_available,
        "onnxruntime_version": onnxruntime_version,
        "onnxruntime_providers": onnxruntime_providers,
        "runner_module": runner_module or None,
        "runner_available": runner_available,
        "runner_error": runner_error,
    }


def _environment_info() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "executable": sys.executable,
    }


def _session_load_report(model_path: Path) -> list[dict[str, Any]]:
    if importlib.util.find_spec("onnxruntime") is None:
        return [{"loaded": False, "error": "onnxruntime_missing"}]
    if not model_path.exists():
        return [{"loaded": False, "error": "model_path_missing", "model_path": str(model_path)}]
    rows: list[dict[str, Any]] = []
    for path in sorted(model_path.rglob("*.onnx")):
        code = f"""
import json
import time
import onnxruntime as ort
started = time.perf_counter()
row = {{"file": {str(path.relative_to(model_path))!r}, "file_size_bytes": {path.stat().st_size}, "loaded": False, "load_time_ms": None, "inputs": [], "outputs": [], "error": None, "onnxruntime_version": getattr(ort, "__version__", None), "providers": ort.get_available_providers()}}
try:
    opts = ort.SessionOptions()
    opts.enable_mem_pattern = False
    sess = ort.InferenceSession({str(path)!r}, sess_options=opts, providers=["CPUExecutionProvider"])
    row["loaded"] = True
    row["load_time_ms"] = int((time.perf_counter() - started) * 1000)
    row["inputs"] = [{{"name": item.name, "shape": [str(value) for value in item.shape], "type": item.type}} for item in sess.get_inputs()]
    row["outputs"] = [{{"name": item.name, "shape": [str(value) for value in item.shape], "type": item.type}} for item in sess.get_outputs()]
except Exception as exc:
    row["load_time_ms"] = int((time.perf_counter() - started) * 1000)
    row["error"] = str(exc)
print(json.dumps(row, ensure_ascii=False))
"""
        started = time.perf_counter()
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        if completed.returncode == 0 and completed.stdout.strip():
            try:
                rows.append(json.loads(completed.stdout.strip().splitlines()[-1]))
                continue
            except json.JSONDecodeError:
                pass
        rows.append(
            {
                "file": str(path.relative_to(model_path)),
                "file_size_bytes": path.stat().st_size,
                "loaded": False,
                "load_time_ms": int((time.perf_counter() - started) * 1000),
                "exit_code": completed.returncode,
                "error": (completed.stderr or completed.stdout or "onnx_session_load_failed").strip()[:2000],
            }
        )
    return rows


def _diagnostic_interpretation(
    bundle: dict[str, Any],
    runtime: dict[str, Any],
    session_load: list[dict[str, Any]],
    *,
    can_infer: bool,
    sample_validated: bool,
) -> dict[str, Any]:
    decoder_rows = [row for row in session_load if str(row.get("file", "")).endswith("decoder_model_merged.onnx")]
    decoder_loaded = any(row.get("loaded") for row in decoder_rows)
    decoder_crashed = any(row.get("exit_code") in {-11, 139} for row in decoder_rows)
    decoder_op_missing = any("NOT_IMPLEMENTED" in str(row.get("error") or "") for row in decoder_rows)
    component_rows = {
        row.get("file"): bool(row.get("loaded"))
        for row in session_load
        if str(row.get("file", "")).endswith((".onnx",))
    }

    if sample_validated:
        provider_available = True
        fallback_reason = None
        artifact_status = "validated_runner_output"
    elif can_infer:
        provider_available = False
        fallback_reason = "paddleocr_vl_onnx_inference_not_validated"
        artifact_status = "runner_available_output_not_validated"
    elif not bundle.get("usable"):
        provider_available = False
        fallback_reason = "model_bundle_missing_or_incomplete"
        artifact_status = "bundle_incomplete"
    elif not runtime.get("onnxruntime_available"):
        provider_available = False
        fallback_reason = "onnxruntime_missing"
        artifact_status = "runtime_missing"
    elif not runtime.get("runner_available"):
        provider_available = False
        fallback_reason = "runner_contract_missing"
        artifact_status = "onnx_sessions_probe_only"
    else:
        provider_available = False
        fallback_reason = "provider_not_ready"
        artifact_status = "unknown"

    compatibility_note = "not_evaluated"
    if session_load:
        if decoder_loaded:
            compatibility_note = "model_artifact_not_fully_broken_runtime_version_can_load_decoder"
        elif decoder_crashed:
            compatibility_note = "decoder_native_crash_on_this_runtime"
        elif decoder_op_missing:
            compatibility_note = "decoder_operator_not_supported_on_this_runtime"
        else:
            compatibility_note = "decoder_not_loaded"

    return {
        "artifact_status": artifact_status,
        "compatibility_note": compatibility_note,
        "provider_available": provider_available,
        "fallback_provider": "paddleocr_ppocrv4",
        "fallback_reason": fallback_reason,
        "decoder_loaded": decoder_loaded,
        "decoder_native_crash": decoder_crashed,
        "decoder_operator_missing": decoder_op_missing,
        "component_load_results": component_rows,
        "runner_contract_required": [
            "preprocess PDF/page image into pixel_values and image_grid_thw",
            "run vision_encoder.onnx",
            "prepare prompt token ids through tokenizer and embed_tokens.onnx",
            "drive decoder_model_merged.onnx autoregressive generation with past_key_values",
            "decode text/markdown output and normalize into OCRResult candidates",
        ],
        "next_action": "Implement minimal Linux ONNX runner with onnxruntime 1.23.2 or 1.20.1, then run 08_image first-text-output smoke.",
    }


def _run_sample(
    sample: Path,
    *,
    model_path: Path,
    runner_module: str,
    timeout_seconds: float,
    max_pages: int,
    max_new_tokens: int,
    prompt: str,
    can_infer: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sample": str(sample),
        "exists": sample.exists(),
        "provider_used": None,
        "fallback_used": True,
        "fallback_reason": None,
        "model_loaded": False,
        "inference_time_ms": None,
        "text_length": 0,
        "line_candidates_count": 0,
        "table_candidates_count": 0,
        "raw_output_preview": None,
        "error": None,
    }
    if not sample.exists():
        row["fallback_reason"] = "sample_missing"
        return row
    if not can_infer:
        row["fallback_reason"] = "vl_onnx_not_ready"
        return row

    started = time.perf_counter()
    try:
        module = importlib.import_module(runner_module)
        output = module.predict(
            image_path=str(sample),
            model_path=str(model_path),
            model_files=[str(path) for path in sorted(model_path.rglob("*.onnx"))],
            device="cpu",
            timeout_seconds=timeout_seconds,
            max_pages=max_pages,
            max_new_tokens=max_new_tokens,
            prompt=prompt,
        )
        row["model_loaded"] = True
        row["inference_time_ms"] = int((time.perf_counter() - started) * 1000)
        row.update(_output_metrics(output, prompt=prompt))
        if row["output_validation_status"] == "candidate_text_generated":
            row["provider_used"] = "paddleocr_vl_onnx_quantized"
            row["fallback_used"] = False
        else:
            row["provider_used"] = None
            row["fallback_used"] = True
            row["fallback_reason"] = row["output_validation_status"]
    except Exception as exc:
        row["fallback_reason"] = getattr(exc, "reason", "runner_inference_failed")
        row["error"] = str(exc)
        row["inference_time_ms"] = int((time.perf_counter() - started) * 1000)
    return row


def _output_metrics(output: Any, *, prompt: str = "") -> dict[str, Any]:
    payload = output if isinstance(output, dict) else {}
    text = str(payload.get("text") or payload.get("markdown") or payload.get("content") or "")
    lines = payload.get("line_candidates") or payload.get("lines") or []
    tables = payload.get("table_candidates") or payload.get("tables") or []
    preview = json.dumps(payload, ensure_ascii=False)[:1200] if payload else str(output)[:1200]
    return {
        "text_length": len(text),
        "text_preview": text[:500],
        "output_validation_status": _validate_output_text(text, prompt=prompt),
        "line_candidates_count": len(lines) if isinstance(lines, list) else 0,
        "table_candidates_count": len(tables) if isinstance(tables, list) else 0,
        "raw_output_preview": preview,
    }


def _validate_output_text(text: str, *, prompt: str = "") -> str:
    return validate_generated_text(text, prompt=prompt)


def _download_commands(repo_id: str, model_path: Path) -> list[str]:
    return [
        f"python3 -m pip install --user -U huggingface_hub",
        f"python3 -m huggingface_hub download {repo_id} --local-dir {model_path} --local-dir-use-symlinks False",
        "docker compose up -d --build backend frontend ocr-worker",
        "docker compose exec backend sh -lc 'ENABLE_PADDLEOCR_VL_ONNX=true python -m app.scripts.smoke_paddleocr_vl_onnx --model-path /app/models/paddleocr_vl_onnx_quantized'",
    ]


def _session_only_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": report["summary"],
        "environment": report.get("environment"),
        "runtime": report.get("runtime"),
        "diagnostic_interpretation": report.get("diagnostic_interpretation"),
        "bundle": {
            "path": report.get("bundle", {}).get("path"),
            "onnx_files": report.get("bundle", {}).get("onnx_files"),
            "onnx_file_count": report.get("bundle", {}).get("onnx_file_count"),
        },
        "onnx_session_load": report.get("onnx_session_load") or [],
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# PaddleOCR-VL ONNX Smoke Report",
        "",
        f"- Status: `{summary['status']}`",
        f"- Repo: `{summary['repo_id']}`",
        f"- License: `{summary.get('license')}`",
        f"- Model path: `{summary['model_path']}`",
        f"- Provider used: `{summary.get('provider_used')}`",
        f"- Fallback required: `{summary.get('fallback_required')}`",
        f"- Fallback reason: `{summary.get('fallback_reason')}`",
        "",
        "## Bundle",
        "",
        f"- Path exists: `{report['bundle']['path_exists']}`",
        f"- ONNX files: `{report['bundle']['onnx_file_count']}`",
        f"- Missing required files: `{', '.join(report['bundle']['missing_required_files']) or 'none'}`",
        "",
        "## Runtime",
        "",
        f"- onnxruntime available: `{report['runtime']['onnxruntime_available']}`",
        f"- onnxruntime version: `{report['runtime'].get('onnxruntime_version')}`",
        f"- onnxruntime providers: `{', '.join(report['runtime'].get('onnxruntime_providers') or []) or 'none'}`",
        f"- runner module: `{report['runtime']['runner_module']}`",
        f"- runner available: `{report['runtime']['runner_available']}`",
        f"- runner error: `{report['runtime']['runner_error']}`",
        "",
        "## ONNX Session Load",
        "",
        "| file | loaded | exit | time_ms | error |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report.get("onnx_session_load") or []:
        lines.append(
            f"| {row.get('file', '')} | {row.get('loaded')} | {row.get('exit_code', '')} | {row.get('load_time_ms', '')} | {str(row.get('error') or '')[:120]} |"
        )
    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| sample | provider | fallback | reason | time_ms | text | lines | tables |",
            "|---|---:|---:|---|---:|---:|---:|---:|",
        ]
    )
    for sample in report["samples"]:
        lines.append(
            "| {sample} | {provider_used} | {fallback_used} | {fallback_reason} | {inference_time_ms} | {text_length} | {line_candidates_count} | {table_candidates_count} |".format(
                **sample
            )
        )
    lines.extend(["", "## Download Commands", ""])
    lines.extend(f"```bash\n{command}\n```" for command in report["download_commands"])
    return "\n".join(lines) + "\n"


def _session_markdown(report: dict[str, Any]) -> str:
    env = report.get("environment") or {}
    runtime = report.get("runtime") or {}
    lines = [
        "# PaddleOCR-VL ONNX Session Report",
        "",
        f"- OS: `{env.get('platform')}`",
        f"- Machine: `{env.get('machine')}`",
        f"- Python: `{env.get('python_version')}`",
        f"- Executable: `{env.get('executable')}`",
        f"- ONNX Runtime: `{runtime.get('onnxruntime_version')}`",
        f"- Providers: `{', '.join(runtime.get('onnxruntime_providers') or []) or 'none'}`",
        f"- Provider available: `{report['summary'].get('provider_used') == 'paddleocr_vl_onnx_quantized'}`",
        f"- Fallback reason: `{report['summary'].get('fallback_reason')}`",
        f"- Diagnostic: `{(report.get('diagnostic_interpretation') or {}).get('compatibility_note')}`",
        f"- Next action: `{(report.get('diagnostic_interpretation') or {}).get('next_action')}`",
        "",
        "| file | size_mb | loaded | exit | time_ms | onnxruntime | providers | inputs | outputs | error |",
        "|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for row in report.get("onnx_session_load") or []:
        inputs = ", ".join(item.get("name", "") for item in row.get("inputs") or [])
        outputs = ", ".join(item.get("name", "") for item in row.get("outputs") or [])
        size_mb = round((row.get("file_size_bytes") or 0) / 1024**2, 1)
        lines.append(
            f"| {row.get('file', '')} | {size_mb} | {row.get('loaded')} | {row.get('exit_code', '')} | {row.get('load_time_ms', '')} | {row.get('onnxruntime_version', '')} | {', '.join(row.get('providers') or [])} | {inputs} | {outputs} | {str(row.get('error') or '')[:160]} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
