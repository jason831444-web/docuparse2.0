from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.file_ingestion import FileIngestionService
from app.services.image_preprocessor import ImagePreprocessor
from app.services.persistence_safety import sanitize_for_postgres
from app.services.vl_candidate_client import VLCandidateWorkerClient


def _candidate_metrics(result: dict[str, Any]) -> dict[str, Any]:
    tables = result.get("tables") if isinstance(result.get("tables"), list) else []
    quality_rows: list[dict[str, Any]] = []
    for table in tables:
        if isinstance(table, dict) and isinstance(table.get("official_table_quality"), dict):
            quality_rows.append(table["official_table_quality"])
    if not quality_rows:
        return {
            "quality_score": 0.0,
            "table_count": len(tables),
            "row_count": 0,
            "expected_column_coverage": 0.0,
            "empty_cell_ratio": 1.0,
        }
    best = max(
        quality_rows,
        key=lambda quality: (
            float(quality.get("quality_score") or 0.0),
            int(quality.get("row_count") or 0),
            -float(quality.get("empty_cell_ratio") if quality.get("empty_cell_ratio") is not None else 1.0),
        ),
    )
    return {
        "quality_score": float(best.get("quality_score") or 0.0),
        "table_count": len(tables),
        "row_count": sum(int(quality.get("row_count") or 0) for quality in quality_rows),
        "expected_column_coverage": float(best.get("expected_column_coverage") or 0.0),
        "empty_cell_ratio": float(best.get("empty_cell_ratio") if best.get("empty_cell_ratio") is not None else 1.0),
    }


def _compare_metrics(original: dict[str, Any], preprocessed: dict[str, Any] | None) -> tuple[str, str]:
    if not preprocessed:
        return "original", "only_original_candidate_available"
    original_tuple = (
        float(original.get("quality_score") or 0.0),
        int(original.get("row_count") or 0),
        -float(original.get("empty_cell_ratio") if original.get("empty_cell_ratio") is not None else 1.0),
        1,
    )
    preprocessed_tuple = (
        float(preprocessed.get("quality_score") or 0.0),
        int(preprocessed.get("row_count") or 0),
        -float(preprocessed.get("empty_cell_ratio") if preprocessed.get("empty_cell_ratio") is not None else 1.0),
        0,
    )
    if preprocessed_tuple > original_tuple:
        if float(preprocessed.get("quality_score") or 0.0) > float(original.get("quality_score") or 0.0):
            return "standard_preprocessed", "higher_quality_score"
        if int(preprocessed.get("row_count") or 0) > int(original.get("row_count") or 0):
            return "standard_preprocessed", "more_table_rows_tie_breaker"
        return "standard_preprocessed", "lower_empty_cell_ratio_tie_breaker"
    if original_tuple[:3] == preprocessed_tuple[:3]:
        return "original", "original_tie_breaker"
    return "original", "higher_or_equal_original_quality"


def _analyze_candidate(
    worker: VLCandidateWorkerClient,
    file_path: Path,
    *,
    original_filename: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = worker.analyze(file_path, original_filename=original_filename)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    metrics = _candidate_metrics(result)
    return {
        "path": str(file_path),
        "ok": bool(result.get("ok")),
        "status": result.get("status") or result.get("classification"),
        "fallback_reason": result.get("fallback_reason"),
        "elapsed_ms": int(result.get("elapsed_ms") or elapsed_ms),
        **metrics,
    }


def compare_vl_input_candidates(
    file_path: Path,
    *,
    output_dir: Path,
    allow_local_worker: bool = False,
) -> dict[str, Any]:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    settings = get_settings()
    worker = VLCandidateWorkerClient()
    if not worker.enabled():
        raise RuntimeError("VL worker is disabled or unconfigured")
    if worker.worker_location == "local" and not allow_local_worker:
        raise RuntimeError("Refusing local VL worker by default; use --allow-local-worker only on RunPod/debug hosts")
    output_dir.mkdir(parents=True, exist_ok=True)
    preprocessor = ImagePreprocessor(FileIngestionService().document_quality)
    variant = preprocessor.prepare_standard_vl_input(file_path, output_dir / "preprocessed")
    original = _analyze_candidate(worker, file_path, original_filename=file_path.name)
    preprocessed: dict[str, Any] | None = None
    processed_path = variant.get("processed_path")
    if processed_path:
        preprocessed = _analyze_candidate(worker, Path(str(processed_path)), original_filename=file_path.name)
    better_candidate, selection_reason = _compare_metrics(original, preprocessed)
    quality_delta = None
    if preprocessed:
        quality_delta = round(float(preprocessed["quality_score"]) - float(original["quality_score"]), 4)
    report = {
        "input_file": str(file_path),
        "worker": {
            "location": worker.worker_location,
            "provider": worker.worker_provider,
            "transport": "multipart_upload",
            "url_host": worker.worker_url_host,
            "timeout_seconds": worker.timeout_seconds,
        },
        "preprocess_variant": {
            "variant_name": variant.get("variant_name"),
            "processed_path_present": bool(processed_path),
            "operations": list(variant.get("operations") or []),
            "warnings": list(variant.get("warnings") or []),
            "error": variant.get("error"),
        },
        "original": original,
        "preprocessed": preprocessed,
        "better_candidate": better_candidate,
        "selection_reason": selection_reason,
        "quality_delta": quality_delta,
    }
    return sanitize_for_postgres(report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare original vs standard-preprocessed PaddleOCRVL official table quality.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/docuparse_vl_input_candidate_comparison"))
    parser.add_argument("--allow-local-worker", action="store_true")
    args = parser.parse_args()
    report = compare_vl_input_candidates(
        args.file,
        output_dir=args.output_dir,
        allow_local_worker=args.allow_local_worker,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"{args.file.stem}.vl_input_candidate_comparison.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
