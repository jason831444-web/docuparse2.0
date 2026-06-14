from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REPO_ID = "lbm364dl/PaddleOCR-VL-1.5-ONNX"
DEFAULT_TARGET = Path("/tmp/docuparse_models/paddleocr_vl_onnx_quantized")
EXPECTED_FILES = [
    "onnx/decoder_model_merged.onnx",
    "onnx/embed_tokens.onnx",
    "onnx/vision_encoder.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer.model",
    "processor_config.json",
    "preprocessor_config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and validate the PaddleOCR-VL ONNX quantized model bundle.")
    parser.add_argument("--repo-id", default=os.getenv("PADDLEOCR_VL_ONNX_REPO_ID", DEFAULT_REPO_ID))
    parser.add_argument("--target", type=Path, default=Path(os.getenv("PADDLEOCR_VL_ONNX_DOWNLOAD_TARGET", str(DEFAULT_TARGET))))
    parser.add_argument("--force", action="store_true", help="Re-run snapshot_download even when expected files already exist.")
    parser.add_argument("--local-files-only", action="store_true", help="Validate an existing local bundle without network access.")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/vl_onnx_download"))
    args = parser.parse_args()

    started = time.perf_counter()
    report = download_or_validate(args.repo_id, args.target, force=args.force, local_files_only=args.local_files_only)
    report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "paddleocr_vl_onnx_download_report.json"
    md_path = args.output_dir / "paddleocr_vl_onnx_download_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    sys.exit(0 if report["summary"]["ok"] else 2)


def download_or_validate(repo_id: str, target: Path, *, force: bool = False, local_files_only: bool = False) -> dict[str, Any]:
    target = target.expanduser().resolve()
    before = inspect_bundle(target)
    repo: dict[str, Any] = {"repo_id": repo_id, "license": None, "error": None}
    download: dict[str, Any] = {"attempted": False, "ok": False, "skipped": False, "error": None}

    if before["complete"] and not force:
        download["skipped"] = True
        after = before
    else:
        if local_files_only:
            download["error"] = "local_files_only_bundle_incomplete"
            after = before
        else:
            download["attempted"] = True
            try:
                from huggingface_hub import HfApi, snapshot_download

                info = HfApi().model_info(repo_id, files_metadata=True)
                card_data = getattr(info, "card_data", None)
                repo["license"] = card_data.get("license") if card_data else None
                target.mkdir(parents=True, exist_ok=True)
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=target,
                    local_dir_use_symlinks=False,
                    allow_patterns=[
                        "onnx/*.onnx",
                        "*.json",
                        "*.model",
                        "README*",
                        "LICENSE*",
                    ],
                )
                download["ok"] = True
            except Exception as exc:
                download["error"] = str(exc)
            after = inspect_bundle(target)

    if repo["license"] is None:
        repo["license"] = _read_license_hint(target)
    summary = {
        "ok": bool(after["complete"]),
        "repo_id": repo_id,
        "target": str(target),
        "license": repo.get("license"),
        "commercial_saas_use": "allowed_by_apache_2_0" if str(repo.get("license", "")).lower() == "apache-2.0" else "review_required",
        "missing_files": after["missing_files"],
        "download_attempted": download["attempted"],
        "download_skipped": download["skipped"],
        "download_error": download["error"],
    }
    return {
        "summary": summary,
        "repo": repo,
        "download": download,
        "before": before,
        "after": after,
        "expected_files": EXPECTED_FILES,
        "next_commands": [
            "docker compose exec backend python -m app.scripts.smoke_paddleocr_vl_onnx --model-path /app/models/paddleocr_vl_onnx_quantized --check-sessions",
            "ENABLE_PADDLEOCR_VL_ONNX=true docker compose up -d --build backend frontend ocr-worker",
        ],
    }


def inspect_bundle(target: Path) -> dict[str, Any]:
    missing = [name for name in EXPECTED_FILES if not (target / name).exists()]
    files = []
    if target.exists():
        for path in sorted(item for item in target.rglob("*") if item.is_file())[:80]:
            files.append({"path": str(path.relative_to(target)), "size_bytes": path.stat().st_size})
    return {
        "target": str(target),
        "path_exists": target.exists(),
        "complete": target.exists() and not missing,
        "missing_files": missing,
        "file_count": len(files),
        "files_preview": files,
    }


def _read_license_hint(target: Path) -> str | None:
    readme = target / "README.md"
    if not readme.exists():
        return None
    text = readme.read_text(encoding="utf-8", errors="ignore").lower()
    if "apache-2.0" in text or "apache 2.0" in text:
        return "apache-2.0"
    return None


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# PaddleOCR-VL ONNX Download Report",
        "",
        f"- OK: `{summary['ok']}`",
        f"- Repo: `{summary['repo_id']}`",
        f"- Target: `{summary['target']}`",
        f"- License: `{summary.get('license')}`",
        f"- Commercial SaaS use: `{summary.get('commercial_saas_use')}`",
        f"- Download attempted: `{summary['download_attempted']}`",
        f"- Download skipped: `{summary['download_skipped']}`",
        f"- Download error: `{summary.get('download_error')}`",
        f"- Missing files: `{', '.join(summary['missing_files']) or 'none'}`",
        "",
        "## Expected Files",
        "",
    ]
    lines.extend(f"- `{item}`" for item in EXPECTED_FILES)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
