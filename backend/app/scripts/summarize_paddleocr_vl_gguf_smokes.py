from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REPORT_NAME = "paddleocr_vl_gguf_smoke_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize PaddleOCR-VL GGUF smoke reports.")
    parser.add_argument("--report", action="append", type=Path, default=[], help="Path to a GGUF smoke JSON report.")
    parser.add_argument(
        "--input-dir",
        action="append",
        type=Path,
        default=[],
        help="Directory containing paddleocr_vl_gguf_smoke_report.json. Can be repeated.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/summary.json"),
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("/tmp/docuparse_e2e_logs/paddleocr_vl_gguf_smoke/summary.md"),
    )
    args = parser.parse_args()

    summary = summarize_reports(_collect_report_paths(args.report, args.input_dir))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    args.output_md.write_text(markdown_summary(summary), encoding="utf-8")
    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_md}")


def _collect_report_paths(report_paths: list[Path], input_dirs: list[Path]) -> list[Path]:
    paths = list(report_paths)
    for directory in input_dirs:
        paths.append(directory / REPORT_NAME)
    return paths


def summarize_reports(paths: list[Path]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    missing_paths: list[str] = []
    for path in paths:
        if not path.exists():
            missing_paths.append(str(path))
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        report["_source_report"] = str(path)
        reports.append(report)

    rows = [_summarize_one(report) for report in reports]
    severity_counts = Counter(row["manual_severity"] for row in rows)
    classification_counts = Counter(row["classification"] for row in rows)
    issue_counts = Counter(code for row in rows for code in row["issue_codes"])
    candidate_count = sum(1 for row in rows if row["provider_available_candidate"])
    visual_checked_count = sum(1 for row in rows if row["pdf_opened_and_visually_checked"])
    production_active_recommended = bool(rows) and all(
        row["provider_available_candidate"] and row["manual_severity"] == "pass" for row in rows
    )

    return {
        "report_count": len(rows),
        "missing_report_paths": missing_paths,
        "provider_available_candidate_count": candidate_count,
        "manual_visual_checked_count": visual_checked_count,
        "manual_severity_counts": dict(sorted(severity_counts.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "production_active_recommended": production_active_recommended,
        "production_active_reason": _production_active_reason(rows, missing_paths),
        "rows": rows,
    }


def _summarize_one(report: dict[str, Any]) -> dict[str, Any]:
    manual_check = report.get("manual_visual_check") or {}
    manual_validation = report.get("manual_visual_check_validation") or {}
    validation = report.get("validation") or {}
    sample = Path(str(report.get("sample") or "")).name or str(report.get("sample") or "")
    issue_codes = [str(code) for code in manual_validation.get("issue_codes") or [] if code]
    resource_monitor = _resource_monitor_summary(report.get("_source_report"))
    return {
        "sample": sample,
        "source_report": report.get("_source_report"),
        "ok": bool(report.get("ok")),
        "classification": report.get("classification") or "unknown",
        "manual_severity": manual_validation.get("severity") or ("fail" if report.get("error") else "unknown"),
        "provider_available_candidate": bool(report.get("provider_available_candidate")),
        "provider_available_decision_reason": report.get("provider_available_decision_reason"),
        "pdf_opened_and_visually_checked": bool(manual_check.get("pdf_opened_and_visually_checked")),
        "matched_terms": validation.get("matched_terms") or [],
        "issue_codes": issue_codes,
        "unique_issue_codes": list(dict.fromkeys(issue_codes)),
        "dangerous_error_count": int(manual_validation.get("dangerous_error_count") or 0),
        "hallucination_count": int(manual_validation.get("hallucination_count") or 0),
        "elapsed_ms": report.get("elapsed_ms"),
        "resource_monitor": resource_monitor,
        "error": report.get("error"),
    }


def _resource_monitor_summary(source_report: Any) -> dict[str, Any] | None:
    if not source_report:
        return None
    path = Path(str(source_report)).parent / "resource_monitor.log"
    if not path.exists():
        return None
    max_mem_used_mib: float | None = None
    max_swap_used_mib: float | None = None
    samples = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] == "Mem:":
            value = _human_size_to_mib(parts[2])
            if value is not None:
                max_mem_used_mib = value if max_mem_used_mib is None else max(max_mem_used_mib, value)
                samples += 1
        elif parts[0] == "Swap:":
            value = _human_size_to_mib(parts[2])
            if value is not None:
                max_swap_used_mib = value if max_swap_used_mib is None else max(max_swap_used_mib, value)
    return {
        "samples": samples,
        "max_mem_used_mib": round(max_mem_used_mib, 1) if max_mem_used_mib is not None else None,
        "max_swap_used_mib": round(max_swap_used_mib, 1) if max_swap_used_mib is not None else None,
        "source": str(path),
    }


def _human_size_to_mib(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    match = re.match(r"(?i)^([0-9]+(?:\.[0-9]+)?)([kmgtp]?i?)?$", value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "m").lower().rstrip("i")
    multiplier = {
        "k": 1 / 1024,
        "m": 1,
        "g": 1024,
        "t": 1024 * 1024,
        "p": 1024 * 1024 * 1024,
        "": 1 / (1024 * 1024),
    }.get(unit)
    if multiplier is None:
        return None
    return amount * multiplier


def _production_active_reason(rows: list[dict[str, Any]], missing_paths: list[str]) -> str:
    if missing_paths:
        return "missing_smoke_reports"
    if not rows:
        return "no_smoke_reports"
    if any(row["manual_severity"] == "fail" for row in rows):
        return "manual_visual_check_failed"
    if any(row["manual_severity"] == "warn" for row in rows):
        return "manual_visual_check_warn"
    if any(not row["provider_available_candidate"] for row in rows):
        return "provider_candidate_rejected"
    return "all_smokes_passed"


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# PaddleOCR-VL GGUF Smoke Summary",
        "",
        f"- Reports: {summary['report_count']}",
        f"- Manual visual checks: {summary['manual_visual_checked_count']}",
        f"- Provider candidate reports: {summary['provider_available_candidate_count']}",
        f"- Production active recommended: `{summary['production_active_recommended']}`",
        f"- Production active reason: `{summary['production_active_reason']}`",
        f"- Manual severity counts: {_format_counts(summary['manual_severity_counts']) or '-'}",
        f"- Classifications: {_format_counts(summary['classification_counts']) or '-'}",
        f"- Issue counts: {_format_counts(summary['issue_counts']) or '-'}",
        "",
        "## Reports",
        "",
        "| Sample | Severity | Candidate | Reason | Issues | Elapsed ms | Source |",
        "|---|---|---:|---|---|---:|---|",
    ]
    for row in summary["rows"]:
        resource = row.get("resource_monitor") or {}
        resource_text = ""
        if resource:
            resource_text = " mem={mem}MiB swap={swap}MiB".format(
                mem=resource.get("max_mem_used_mib"),
                swap=resource.get("max_swap_used_mib"),
            )
        lines.append(
            "| {sample} | {manual_severity} | {provider_available_candidate} | {provider_available_decision_reason} | {issues} | {elapsed_ms}{resource_text} | {source_report} |".format(
                sample=row["sample"],
                manual_severity=row["manual_severity"],
                provider_available_candidate=row["provider_available_candidate"],
                provider_available_decision_reason=row.get("provider_available_decision_reason") or "",
                issues=", ".join(row["unique_issue_codes"]),
                elapsed_ms=row.get("elapsed_ms") or "",
                resource_text=resource_text,
                source_report=row.get("source_report") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} x{value}" for key, value in sorted(counts.items()))


if __name__ == "__main__":
    main()
