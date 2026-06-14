from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


DEFAULT_REPO_ID = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
EXPECTED_FILES = [
    "PaddleOCR-VL-1.6-GGUF.gguf",
    "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
    "README.md",
    "chat_template.jinja",
]


def _sha256(path: Path) -> str:
    return subprocess.check_output(["sha256sum", str(path)], text=True).split()[0]


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "paddleocr_vl_gguf_download_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# PaddleOCR-VL GGUF Download Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Repo: `{report.get('repo_id')}`",
        f"- Target: `{report.get('target')}`",
        f"- License: `{report.get('license')}`",
        f"- Error: `{report.get('error')}`",
        "",
        "## Files",
        "",
    ]
    for item in report.get("files") or []:
        lines.append(f"- `{item['name']}`: `{item['size_bytes']}` bytes")
    (output_dir / "paddleocr_vl_gguf_download_report.md").write_text("\n".join(lines), encoding="utf-8")


def download(repo_id: str, target: Path, output_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    report: dict[str, Any] = {
        "ok": False,
        "repo_id": repo_id,
        "target": str(target),
        "expected_files": EXPECTED_FILES,
        "files": [],
        "license": "Apache-2.0",
        "error": None,
    }
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        report["error"] = f"huggingface_hub_unavailable: {exc}"
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report(output_dir, report)
        return report

    target.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(target),
            allow_patterns=["*.gguf", "README.md", "chat_template.jinja", ".gitattributes"],
        )
    except Exception as exc:
        report["error"] = f"download_failed: {exc}"
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report(output_dir, report)
        return report

    missing = [name for name in EXPECTED_FILES if not (target / name).exists()]
    files = []
    for path in sorted(target.iterdir()):
        if path.is_file():
            files.append({"name": path.name, "size_bytes": path.stat().st_size})
    report["files"] = files
    if missing:
        report["error"] = f"bundle_incomplete: missing {', '.join(missing)}"
        report["missing_files"] = missing
        report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report(output_dir, report)
        return report

    manifest = target / "SHA256SUMS.txt"
    manifest.write_text(
        "\n".join(f"{_sha256(target / name)}  {name}" for name in EXPECTED_FILES if name.endswith(".gguf")) + "\n",
        encoding="utf-8",
    )
    report["sha256_manifest"] = str(manifest)
    report["ok"] = True
    report["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    _write_report(output_dir, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the official PaddleOCR-VL-1.6 GGUF model bundle.")
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--target", type=Path, default=Path("/root/docuparse_models/paddleocr_vl_1_6_gguf"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_download"))
    args = parser.parse_args()

    report = download(args.repo_id, args.target, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
