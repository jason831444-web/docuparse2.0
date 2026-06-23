from __future__ import annotations

import base64
import inspect
import json
import logging
import mimetypes
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from pydantic import BaseModel
import requests

from app.core.config import get_settings
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata, extract_text, validate_output_text
from app.services.canonical_schema import (
    canonical_field_for_header,
    canonicalize_official_table_row,
    expected_column_groups,
    inspection_header_or_note,
)
from app.services.image_preprocessor import ImagePreprocessor


app = FastAPI(title="Docparse PaddleOCR-VL GGUF Worker")
logger = logging.getLogger(__name__)

_pipeline: Any | None = None
_pipeline_lock = threading.Lock()
_inference_lock = threading.Lock()
_last_error: str | None = None
_last_request_at: str | None = None
_last_success_at: str | None = None


VLM_STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "version": "docparse_vl_table_schema_v1",
    "response_fields": ["raw_text", "key_values", "tables"],
    "key_value_fields": {
        "required": ["key", "value"],
        "optional": ["bbox", "key_bbox", "value_bbox", "page_index", "confidence"],
        "bbox_format": "normalized [x1, y1, x2, y2] floats from 0 to 1, relative to the rendered page",
        "scope": "visible non-table key-value labels and values only",
    },
    "table_types": {
        "incoming_inspection": {
            "columns": [
                "no",
                "item_name",
                "lot_code",
                "document_item_code",
                "specification",
                "received_quantity",
                "accepted_quantity",
                "defective_quantity",
                "inspection_item",
                "result",
                "note",
            ],
            "amount_fields": "must_be_null",
            "visibility_policy": "do_not_infer_hidden_values",
            "review_policy": "review_required_until_human_confirms",
        }
    },
    "uncertainty_policy": [
        "Keep unseen values null.",
        "Do not create amount, tax, or total fields for inspection reports.",
        "Flag O/0, 주/(주), 유동/유통, 검사/경사 and similar OCR uncertainty for review.",
    ],
}

VLM_TABLE_EXTRACTION_PROMPT = """You are Docparse's manufacturing document VLM table extractor.

Read the provided document image visually. Return ONLY valid JSON, no markdown.
The first character of your response must be "{" and the last character must be "}".
Do not explain the document in prose. Do not output a markdown table.

Required JSON shape:
{
  "raw_text": "verbatim readable text you can see",
  "document_type": "inspection_report | delivery_note | purchase_order | quotation | invoice | transaction_statement | internal_transfer | return_credit | unknown",
  "key_values": [
    {
      "key": "visible label exactly as printed, e.g. 문서번호",
      "value": "visible value exactly as printed, e.g. DOC-007",
      "bbox": [0.00, 0.00, 0.00, 0.00],
      "key_bbox": [0.00, 0.00, 0.00, 0.00],
      "value_bbox": [0.00, 0.00, 0.00, 0.00],
      "page_index": 0,
      "confidence": 0.0
    }
  ],
  "tables": [
    {
      "table_type": "incoming_inspection | line_items | unknown",
      "review_required": true,
      "columns": ["..."],
      "rows": [
        {
          "no": null,
          "item_name": null,
          "lot_code": null,
          "document_item_code": null,
          "specification": null,
          "received_quantity": null,
          "accepted_quantity": null,
          "defective_quantity": null,
          "inspection_item": null,
          "result": null,
          "note": null
        }
      ],
      "warnings": []
    }
  ],
  "warnings": []
}

Rules:
- The primary output is the "tables" array. Put every visible table row there.
- Also output "key_values" for visible non-table labels and values such as 문서번호, 샘플번호, 발행일, 요청부서, 출고창고, 입고창고, 담당, 업체명, 합계.
- For every key-value, include bbox as normalized [x1,y1,x2,y2] coordinates for the full key-value visual span when you can see its location.
- If possible, also include key_bbox for the label span and value_bbox for the value span. Omit a bbox field only when you cannot locate it visually.
- Do not include table cells as key_values unless the document visually presents them as summary fields outside the item table.
- If the document is an incoming inspection / inspection report, extract the visible table as table_type "incoming_inspection".
- Keep unseen values null. Do not infer hidden, cropped, or unreadable cells.
- For inspection reports, do not create amount, unit_price, supply_amount, tax_amount, line_total, subtotal, total, or currency.
- Keep the output review_required=true when handwriting, blur, row boundary uncertainty, or OCR ambiguity exists.
- Flag O/0, 주/(주), 유동/유통, 검사/경사 and similar uncertainty in warnings/review flags.
- Do not merge header, footer, stamp, or summary text into item rows.
"""

VLM_TABLE_JSON_REPAIR_PROMPT = """Convert the following VLM extraction output into ONLY valid JSON for Docparse.

Return the same JSON shape used by the table extractor:
{
  "raw_text": "...",
  "document_type": "inspection_report | delivery_note | purchase_order | quotation | invoice | transaction_statement | internal_transfer | return_credit | unknown",
  "key_values": [
    {
      "key": "...",
      "value": "...",
      "bbox": [0.0, 0.0, 0.0, 0.0],
      "key_bbox": [0.0, 0.0, 0.0, 0.0],
      "value_bbox": [0.0, 0.0, 0.0, 0.0],
      "page_index": 0,
      "confidence": 0.0
    }
  ],
  "tables": [
    {
      "table_type": "incoming_inspection | line_items | unknown",
      "review_required": true,
      "columns": ["..."],
      "rows": []
    }
  ],
  "warnings": []
}

Rules:
- Use only values present in the VLM output below.
- Preserve key_values and their bbox/key_bbox/value_bbox if they are present.
- Preserve table rows as JSON rows.
- For inspection reports, do not add amount, unit_price, supply_amount, tax_amount, line_total, subtotal, total, or currency.
- If a cell is not visible or not present, keep it null or omit it.
- Return JSON only. No prose. No markdown.

VLM output to convert:
"""


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
        "schema_prompt_enabled": getattr(settings, "paddleocr_vl_gguf_schema_prompt_enabled", True),
        "direct_schema_prompt_enabled": getattr(settings, "paddleocr_vl_gguf_direct_schema_prompt_enabled", True),
        "worker_transport": "multipart_upload",
        "last_error": _last_error,
        "last_request_at": _last_request_at,
        "last_success_at": _last_success_at,
    }


@app.post("/analyze")
def analyze(request: VLAnalyzeRequest) -> dict[str, Any]:
    return _analyze_path(
        Path(request.file_path),
        original_filename=request.original_filename,
        transport_metadata={"mode": "shared_file_path"},
    )


@app.post("/analyze-upload")
async def analyze_upload(
    file: UploadFile = File(...),
    original_filename: str | None = Form(None),
) -> dict[str, Any]:
    global _last_error
    settings = get_settings()
    filename = original_filename or file.filename or "upload.bin"
    upload_dir = settings.upload_dir / "vl_remote_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}-{_safe_upload_filename(filename)}"
    uploaded_bytes = 0
    try:
        with saved_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                uploaded_bytes += len(chunk)
                output.write(chunk)
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        logger.exception("PaddleOCR-VL worker upload failed for %s", filename)
        return _base_report(
            saved_path,
            original_filename=filename,
            transport_metadata={
                "mode": "multipart_upload",
                "uploaded_bytes": uploaded_bytes,
                "saved_path": str(saved_path),
            },
            started=time.perf_counter(),
            error=_last_error,
            decision_reason="vl_worker_upload_error",
        )
    finally:
        await file.close()

    return _analyze_path(
        saved_path,
        original_filename=filename,
        transport_metadata={
            "mode": "multipart_upload",
            "uploaded_bytes": uploaded_bytes,
            "saved_path": str(saved_path),
            "content_type": file.content_type,
        },
    )


def _analyze_path(
    path: Path,
    *,
    original_filename: str | None = None,
    transport_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _last_error, _last_request_at, _last_success_at
    started = time.perf_counter()
    _last_request_at = _utc_now_iso()
    report = _base_report(
        path,
        original_filename=original_filename,
        transport_metadata=transport_metadata,
        started=started,
    )
    try:
        if not path.exists():
            raise FileNotFoundError(f"file_path_not_found: {path}")
        image_path = _prepare_input_image(path)
        settings = get_settings()
        schema_metadata: dict[str, Any] = {
            "enabled": bool(getattr(settings, "paddleocr_vl_gguf_schema_prompt_enabled", False)),
            "official_table_source": "paddleocrvl_official_table_html",
            "official_table_count": 0,
        }
        schema_payload: dict[str, Any] | None = None
        tables: list[dict[str, Any]] = []
        key_values: list[dict[str, Any]] = []
        with _inference_lock:
            variant_result = _select_best_full_page_vl_variant(
                image_path,
                settings,
                original_filename=original_filename or path.name,
            )
        output = variant_result["output"]
        text = str(variant_result.get("text") or "")
        tables = list(variant_result.get("tables") or [])
        key_values = list(variant_result.get("key_values") or [])
        schema_metadata["official_table_count"] = len(tables)
        if schema_metadata["enabled"]:
            selected_image_path = Path(variant_result.get("image_path") or image_path)
            prompt_payload, prompt_metadata = _run_schema_prompt_inference(selected_image_path, settings)
            schema_payload = prompt_payload
            schema_metadata.update(prompt_metadata)
            if schema_payload:
                key_values = _key_values_from_schema_payload(
                    schema_payload,
                    source=schema_metadata.get("key_value_source") or schema_metadata.get("table_source") or "vl_schema_prompt",
                )
        if tables:
            if schema_payload and key_values:
                text = str(schema_payload.get("raw_text") or text or "")
                output = [{"text": text, "structured_json": schema_payload, "source_output": output}]
            schema_metadata.update(
                {
                    "used": True,
                    "transport": "paddleocrvl_predict_official_result",
                    "table_source": "paddleocrvl_official_table_html",
                    "prompt_bypassed": not bool(schema_payload),
                }
            )
        else:
            if schema_payload:
                text = str(schema_payload.get("raw_text") or text or "")
                tables = _tables_from_schema_payload(schema_payload, source=schema_metadata.get("table_source") or "vl_schema_prompt")
                output = [{"text": text, "structured_json": schema_payload, "source_output": output}]
            else:
                schema_json = _schema_json_from_output(output)
                if schema_json:
                    schema_metadata.update({"used": True, "transport": "paddleocr_predict_prompt"})
                    text = str(schema_json.get("raw_text") or text)
                    tables = _tables_from_schema_payload(schema_json, source="vl_schema_prompt")
                    key_values = _key_values_from_schema_payload(schema_json, source="vl_schema_prompt")
                elif schema_metadata.get("attempted") and text.strip():
                    schema_metadata["repair_attempted"] = True
                    repaired, repair_error = _run_llama_schema_json_repair(text, settings)
                    repaired_tables = _tables_from_schema_payload(repaired or {}, source="vl_schema_prompt_repair") if repaired else []
                    repaired_key_values = _key_values_from_schema_payload(repaired or {}, source="vl_schema_prompt_repair") if repaired else []
                    if repaired and (repaired_tables or repaired_key_values):
                        schema_metadata.update(
                            {
                                "used": True,
                                "transport": "paddleocr_predict_text_schema_repair",
                                "repair_used": True,
                                "table_source": "vl_schema_prompt_repair",
                                "key_value_source": "vl_schema_prompt_repair",
                            }
                        )
                        text = str(repaired.get("raw_text") or text)
                        tables = repaired_tables
                        key_values = repaired_key_values
                        output = [{"text": text, "structured_json": repaired, "source_output": output}]
                    else:
                        schema_metadata["repair_error"] = repair_error
        if schema_payload and not key_values:
            key_values = _key_values_from_schema_payload(
                schema_payload,
                source=schema_metadata.get("key_value_source") or schema_metadata.get("table_source") or "vl_schema_prompt",
            )
        if schema_payload and key_values:
            key_values = _dedupe_key_values([
                *key_values,
                *_key_values_from_schema_payload(
                    schema_payload,
                    source=schema_metadata.get("key_value_source") or schema_metadata.get("table_source") or "vl_schema_prompt",
                ),
            ])
        validation = validate_output_text(text, [])
        official_table_available = bool(tables)
        key_value_available = bool(key_values)
        readable_output = bool(validation.get("ok") or official_table_available or key_value_available)
        if not tables:
            tables = _extract_structured_tables(text, output, original_filename=original_filename or path.name)
        if readable_output:
            _last_error = None
            _last_success_at = _utc_now_iso()
        report.update(
            {
                "ok": readable_output,
                "classification": validation.get("status") or ("warn" if official_table_available else None),
                "validation": validation,
                "render": {
                    "image_path": str(variant_result.get("image_path") or image_path),
                    "original_image_path": str(image_path),
                    "vl_full_page_variant": variant_result.get("variant_name"),
                    "vl_full_page_variant_comparison": variant_result.get("comparison"),
                    "vl_full_page_preprocess": variant_result.get("preprocess"),
                },
                "text_preview": text[:5000],
                "structured_schema": VLM_STRUCTURED_OUTPUT_SCHEMA,
                "schema_prompt": schema_metadata,
                "key_values": key_values,
                "tables": tables,
                "provider_available_candidate": readable_output,
                "provider_available_decision_reason": (
                    "paddleocrvl_official_table_available"
                    if official_table_available and not validation.get("ok")
                    else "vl_schema_prompt_key_values_available"
                    if key_value_available and not validation.get("ok")
                    else "vl_worker_output_readable"
                    if validation.get("ok")
                    else "vl_worker_output_invalid"
                ),
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


def _base_report(
    path: Path,
    *,
    original_filename: str | None,
    transport_metadata: dict[str, Any] | None,
    started: float,
    error: str | None = None,
    decision_reason: str = "worker_not_completed",
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "provider": "paddleocr_vl_1_6_gguf",
        "source": "vl_worker_api",
        "sample": str(path),
        "original_filename": original_filename,
        "provider_available_candidate": False,
        "provider_available_decision_reason": decision_reason,
        "worker_transport": (transport_metadata or {}).get("mode"),
        "worker_location": "worker_runtime",
        "worker_provider": "vl_worker_api",
        "model_name": "PaddleOCR-VL-1.6-GGUF",
        "n_predict": getattr(get_settings(), "paddleocr_vl_gguf_n_predict", 512),
        "schema_prompt_enabled": getattr(get_settings(), "paddleocr_vl_gguf_schema_prompt_enabled", True),
        "remote_upload": transport_metadata or {},
        "manual_visual_check": {
            "sample": str(path),
            "pdf_opened_and_visually_checked": False,
            "notes": "Upload pipeline candidate; manual visual check has not been performed.",
        },
    }
    if error:
        report.update(
            {
                "classification": "error",
                "error": error,
                "fallback_reason": error,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "candidate_metadata": {"vl_candidates": [], "vl_candidate_summary": {"candidate_count": 0}},
            }
        )
    return report


def _safe_upload_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "upload.bin"
    name = re.sub(r"[^A-Za-z0-9가-힣._ -]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:160] or "upload.bin"


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _prepare_input_image(path: Path) -> Path:
    if path.suffix.casefold() == ".pdf":
        return _render_first_page(path)
    return path


def _select_best_full_page_vl_variant(
    image_path: Path,
    settings: Any,
    *,
    original_filename: str,
) -> dict[str, Any]:
    variants = _full_page_vl_variants(image_path, settings)
    results: list[dict[str, Any]] = []
    for variant in variants:
        candidate_path = Path(variant.get("processed_path") or variant.get("path") or image_path)
        output = _predict_with_optional_paddle_schema_prompt(candidate_path, settings)
        text = extract_text(output)
        tables = _tables_from_official_paddle_output(output, text, original_filename=original_filename)
        key_values = _key_values_from_official_paddle_output(output, image_path=candidate_path)
        score = _vl_variant_score(text, tables, key_values)
        results.append(
            {
                "variant_name": variant.get("variant_name") or "original_full_page",
                "image_path": str(candidate_path),
                "output": output,
                "text": text,
                "tables": tables,
                "key_values": key_values,
                "score": score,
                "preprocess": variant,
            }
        )
    selected = max(results, key=lambda item: item["score"]["total_score"]) if results else {}
    selected["comparison"] = [
        {
            "variant_name": item.get("variant_name"),
            "image_path": item.get("image_path"),
            "score": item.get("score"),
            "preprocess_operations": (item.get("preprocess") or {}).get("operations") or [],
            "preprocess_warnings": (item.get("preprocess") or {}).get("warnings") or [],
        }
        for item in results
    ]
    return selected


def _full_page_vl_variants(image_path: Path, settings: Any) -> list[dict[str, Any]]:
    original = {
        "variant_name": "original_full_page",
        "path": str(image_path),
        "processed_path": str(image_path),
        "operations": ["original_full_page"],
        "warnings": ["no_crop_applied"],
    }
    if not _is_readable_image(image_path):
        return [original]
    output_dir = Path(getattr(settings, "upload_dir", image_path.parent)) / "vl_page_crop_inputs"
    candidate = ImagePreprocessor().prepare_document_page_crop_vl_input(image_path, output_dir)
    candidate_path = str(candidate.get("processed_path") or candidate.get("path") or "")
    if candidate_path and Path(candidate_path).exists():
        return [candidate]
    return [original]


def _is_readable_image(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _vl_variant_score(text: str, tables: list[dict[str, Any]], key_values: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = _key_value_core_coverage(key_values)
    kv_bbox_count = sum(1 for item in key_values if item.get("bbox") or item.get("normalized_bbox") or item.get("key_bbox") or item.get("value_bbox"))
    table_rows = sum(len(table.get("rows") or []) for table in tables if isinstance(table, dict))
    text_len = min(len(str(text or "").strip()), 5000)
    total_score = (
        len(coverage) * 100
        + kv_bbox_count * 12
        + len(key_values) * 5
        + len(tables) * 10
        + table_rows * 2
        + text_len / 1000
    )
    return {
        "total_score": round(total_score, 4),
        "core_fields_covered": sorted(coverage),
        "core_field_count": len(coverage),
        "key_value_count": len(key_values),
        "key_value_bbox_count": kv_bbox_count,
        "table_count": len(tables),
        "table_row_count": table_rows,
        "text_length": len(str(text or "").strip()),
    }


def _key_value_core_coverage(key_values: list[dict[str, Any]]) -> set[str]:
    keys = [re.sub(r"\s+", "", str(item.get("key") or "")).casefold() for item in key_values]
    coverage: set[str] = set()
    if any(re.search(r"문서번호|document(?:number|no)|doc(?:number|no)", key) for key in keys):
        coverage.add("document_number")
    if any(re.search(r"샘플번호|sample", key) for key in keys):
        coverage.add("sample_number")
    if any(re.search(r"작성일|발행일|견적일|거래일자|요청일|invoice(?:date)?|date", key) for key in keys):
        coverage.add("date")
    if any(re.search(r"공급자|seller|vendor", key) for key in keys):
        coverage.add("supplier")
    if any(re.search(r"공급받는자|고객|buyer|customer", key) for key in keys):
        coverage.add("customer")
    if any(re.search(r"합계|total|amount", key) for key in keys):
        coverage.add("total")
    return coverage


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


def _run_schema_prompt_inference(image_path: Path, settings: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "enabled": True,
        "attempted": False,
        "used": False,
        "transport": None,
        "error": None,
        "table_json_primary": True,
        "repair_attempted": False,
        "repair_used": False,
        "raw_response_preview": None,
    }
    if getattr(settings, "paddleocr_vl_gguf_direct_schema_prompt_enabled", False):
        metadata["attempted"] = True
        payload, error, raw_content = _run_direct_llama_schema_prompt(image_path, settings)
        if payload:
            metadata.update({"used": True, "transport": "llama_server_chat_completions", "table_source": "vl_schema_prompt"})
            return payload, metadata
        if raw_content:
            metadata["raw_response_preview"] = raw_content[:1200]
            metadata["repair_attempted"] = True
            repaired, repair_error = _run_llama_schema_json_repair(raw_content, settings)
            if repaired and _tables_from_schema_payload(repaired, source="vl_schema_prompt_repair"):
                metadata.update(
                    {
                        "used": True,
                        "transport": "llama_server_chat_completions_repair",
                        "repair_used": True,
                        "table_source": "vl_schema_prompt_repair",
                    }
                )
                return repaired, metadata
            metadata["repair_error"] = repair_error
        metadata["error"] = error
    return None, metadata


def _run_direct_llama_schema_prompt(image_path: Path, settings: Any) -> tuple[dict[str, Any] | None, str | None, str | None]:
    server_url = str(getattr(settings, "paddleocr_vl_gguf_server_url", "") or "").rstrip("/")
    if not server_url:
        return None, "missing_llama_server_url", None
    endpoint = f"{server_url}/chat/completions"
    image_url = _image_data_url(image_path)
    body = {
        "model": getattr(settings, "paddleocr_vl_gguf_model_file", "PaddleOCR-VL-1.6-GGUF.gguf"),
        "temperature": 0,
        "max_tokens": max(int(getattr(settings, "paddleocr_vl_gguf_n_predict", 512) or 512), 1024),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VLM_TABLE_EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }
    try:
        response = requests.post(
            endpoint,
            json=body,
            timeout=max(30.0, float(getattr(settings, "paddleocr_vl_gguf_timeout_seconds", 240.0))),
        )
        response.raise_for_status()
        data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = _parse_json_object(content)
        if not parsed:
            return None, "schema_prompt_response_not_json", content
        return parsed, None, content
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", None


def _run_llama_schema_json_repair(content: str, settings: Any) -> tuple[dict[str, Any] | None, str | None]:
    server_url = str(getattr(settings, "paddleocr_vl_gguf_server_url", "") or "").rstrip("/")
    if not server_url:
        return None, "missing_llama_server_url"
    endpoint = f"{server_url}/chat/completions"
    body = {
        "model": getattr(settings, "paddleocr_vl_gguf_model_file", "PaddleOCR-VL-1.6-GGUF.gguf"),
        "temperature": 0,
        "max_tokens": max(int(getattr(settings, "paddleocr_vl_gguf_n_predict", 512) or 512), 1024),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": f"{VLM_TABLE_JSON_REPAIR_PROMPT}\n{str(content or '')[:8000]}",
            }
        ],
    }
    try:
        response = requests.post(
            endpoint,
            json=body,
            timeout=max(30.0, float(getattr(settings, "paddleocr_vl_gguf_timeout_seconds", 240.0))),
        )
        response.raise_for_status()
        data = response.json()
        repaired_content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = _parse_json_object(repaired_content)
        if not parsed:
            return None, "schema_repair_response_not_json"
        return parsed, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _predict_with_optional_paddle_schema_prompt(image_path: Path, settings: Any) -> Any:
    pipeline = _get_pipeline()
    kwargs: dict[str, Any] = {"max_new_tokens": getattr(settings, "paddleocr_vl_gguf_n_predict", 512)}
    return _materialize_predict_output(pipeline.predict(str(image_path), **kwargs))


def _materialize_predict_output(output: Any) -> Any:
    """Preserve PaddleOCRVL generator results for multiple downstream readers.

    PaddleOCRVL.predict() can return an iterator of result objects.  The worker
    reads the same output twice: first for text, then for official table blocks.
    Materializing iterators here prevents the first reader from exhausting the
    only copy of the official result.
    """

    if isinstance(output, (str, bytes, dict, list, tuple)):
        return output
    if isinstance(output, Iterator):
        return list(output)
    return output


def _schema_json_from_output(output: Any) -> dict[str, Any] | None:
    candidates: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            structured = value.get("structured_json")
            if isinstance(structured, dict):
                candidates.append(json.dumps(structured, ensure_ascii=False))
            for key in ("json", "structured_output", "text", "content", "markdown"):
                content = value.get(key)
                if isinstance(content, str):
                    candidates.append(content)
                elif isinstance(content, dict):
                    candidates.append(json.dumps(content, ensure_ascii=False))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            candidates.append(value)

    walk(output)
    for candidate in candidates:
        parsed = _parse_json_object(candidate)
        if parsed and (isinstance(parsed.get("tables"), list) or isinstance(parsed.get("key_values"), list)):
            return parsed
    return None


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._current_cell = []
        elif tag == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"td", "th"} and self._current_cell is not None:
            value = _clean_cell("".join(self._current_cell))
            if self._current_row is not None:
                self._current_row.append(value or "")
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(_clean_cell(cell) for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def _tables_from_official_paddle_output(output: Any, text: str, *, original_filename: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for block in _official_parsing_blocks(output):
        if str(block.get("block_label") or "").casefold() != "table":
            continue
        html_table = str(block.get("block_content") or "")
        if not re.search(r"<\s*table\b", html_table, flags=re.IGNORECASE):
            continue
        parsed_rows = _parse_html_table_rows(html_table)
        if len(parsed_rows) < 2:
            continue
        columns = [_clean_cell(cell) or "" for cell in parsed_rows[0]]
        raw_rows = [[_clean_cell(cell) or "" for cell in row] for row in parsed_rows[1:]]
        table_type = _guess_official_table_type(columns, raw_rows, text, original_filename)
        canonical_rows = [_canonical_row_from_official_table(columns, row, table_type) for row in raw_rows]
        canonical_rows = [row for row in canonical_rows if row]
        if not canonical_rows:
            continue
        warnings = ["paddleocrvl_official_table_review_required"]
        if table_type == "incoming_inspection":
            warnings.extend(["inspection_report_no_amount_fields", "vl_schema_prompt_inspection_review_required"])
        table_quality = _official_table_quality(
            columns=columns,
            raw_rows=raw_rows,
            canonical_rows=canonical_rows,
            table_type=table_type,
            text=text,
            original_filename=original_filename,
        )
        if table_quality.get("quality_score", 1.0) < 0.55:
            warnings.append("official_table_quality_low")
        tables.append(
            {
                "table_type": table_type,
                "source": "paddleocrvl_official_table_html",
                "schema_version": VLM_STRUCTURED_OUTPUT_SCHEMA["version"],
                "columns": columns,
                "raw_columns": columns,
                "raw_rows": raw_rows,
                "rows": canonical_rows,
                "warnings": sorted(set(warnings)),
                "review_required": True,
                "official_table_quality": table_quality,
                "amount_fields_policy": "null_for_inspection_report" if table_type == "incoming_inspection" else "do_not_infer_hidden_values",
                "provenance": {
                    "source_type": "vl_source",
                    "mode": "paddleocrvl_official_table_html",
                    "visible": True,
                    "review_required": True,
                    "quality_score": table_quality.get("quality_score"),
                    "expected_column_coverage": table_quality.get("expected_column_coverage"),
                    "block_bbox": block.get("block_bbox"),
                    "block_polygon_points": block.get("block_polygon_points"),
                    "block_label": block.get("block_label"),
                },
            }
        )
    table_count = len(tables)
    for table in tables:
        quality = table.get("official_table_quality")
        if isinstance(quality, dict):
            quality["table_count"] = table_count
    return tables


def _key_values_from_official_paddle_output(
    output: Any,
    *,
    image_path: Path | None = None,
    width: int | None = None,
    height: int | None = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    full_width: int | None = None,
    full_height: int | None = None,
) -> list[dict[str, Any]]:
    if image_path is not None and (width is None or height is None):
        width, height = _image_size(image_path)
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    full_width = max(1, int(full_width or width))
    full_height = max(1, int(full_height or height))
    key_values: list[dict[str, Any]] = []
    section: str | None = None
    for block in _official_parsing_blocks(output):
        label = str(block.get("block_label") or "").casefold()
        text = str(block.get("block_content") or "")
        if not text:
            continue
        block_section = _official_single_section_block(text)
        if block_section:
            section = block_section
            continue
        bbox = _normalize_official_block_bbox(
            block,
            width=width,
            height=height,
            origin_x=origin_x,
            origin_y=origin_y,
            full_width=full_width,
            full_height=full_height,
        )
        if label == "table":
            entries = _key_value_entries_from_official_table_block(text, bbox, initial_section=section)
            source = "vl_block_postprocess_bbox"
            vl_source = "paddleocrvl_official_table_block_postprocess"
        else:
            entries = _key_value_entries_from_official_text_block(text, bbox, initial_section=section)
            source = "vl_text_block_key_value_bbox"
            vl_source = "paddleocrvl_official_text_block"
        for key, value, item_bbox, key_bbox, value_bbox in entries:
            item = {
                "key": key,
                "value": value,
                "source": source,
                "bbox_source": source,
                "vl_source": vl_source,
                "bbox": item_bbox,
                "key_bbox": key_bbox,
                "value_bbox": value_bbox,
                "page_index": 0,
            }
            key_values.append({field: field_value for field, field_value in item.items() if field_value not in (None, "", [])})
    return _dedupe_key_values(key_values)


def _image_size(image_path: Path) -> tuple[int, int]:
    try:
        with Image.open(image_path) as image:
            return max(1, int(image.width)), max(1, int(image.height))
    except Exception:
        return 1, 1


def _key_value_entries_from_official_text_block(
    text: str,
    bbox: list[float] | None,
    *,
    initial_section: str | None = None,
) -> list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None]]:
    raw_lines = [line.strip() for line in str(text or "").splitlines()]
    lines = [line for line in raw_lines if line]
    if not lines:
        cleaned = _clean_cell(str(text or ""))
        lines = [cleaned] if cleaned else []
    section: str | None = initial_section
    entries: list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None]] = []
    value_line_count = max(1, len(lines))
    for index, line in enumerate(lines):
        next_section = _official_section_from_text(line)
        if next_section:
            section = next_section
            continue
        line_bbox = _slice_normalized_bbox_by_line(bbox, index, value_line_count)
        for key, value, start, end in _parse_official_key_value_text(line):
            full_key = _sectioned_official_key(section, key)
            item_bbox = _slice_normalized_bbox_by_span(line, line_bbox, start, end)
            key_bbox, value_bbox = _split_normalized_key_value_bbox(line[start:end], key, item_bbox)
            entries.append((full_key, value, item_bbox, key_bbox, value_bbox))
    return entries


def _key_value_entries_from_official_table_block(
    html_table: str,
    bbox: list[float] | None,
    *,
    initial_section: str | None = None,
) -> list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None]]:
    rows = _parse_html_table_rows(html_table)
    if not _looks_like_key_value_table(rows):
        return []
    section: str | None = initial_section
    entries: list[tuple[str, str, list[float] | None, list[float] | None, list[float] | None]] = []
    value_rows = [row for row in rows if row and _clean_cell(row[0])]
    row_count = max(1, len(value_rows))
    for index, row in enumerate(value_rows):
        line = _clean_cell(row[0])
        if not line:
            continue
        next_section = _official_section_from_text(line)
        if next_section:
            section = next_section
            continue
        row_bbox = _slice_normalized_bbox_by_line(bbox, index, row_count)
        for key, value, start, end in _parse_official_key_value_text(line):
            full_key = _sectioned_official_key(section, key)
            item_bbox = _slice_normalized_bbox_by_span(line, row_bbox, start, end)
            key_bbox, value_bbox = _split_normalized_key_value_bbox(line[start:end], key, item_bbox)
            entries.append((full_key, value, item_bbox, key_bbox, value_bbox))
    return entries


def _looks_like_key_value_table(rows: list[list[str]]) -> bool:
    if not rows:
        return False
    non_empty_rows = [[_clean_cell(cell) or "" for cell in row] for row in rows if any(_clean_cell(cell) for cell in row)]
    if not non_empty_rows:
        return False
    if max(len(row) for row in non_empty_rows) > 2:
        return False
    joined = "\n".join(" ".join(row) for row in non_empty_rows)
    if re.search(r"No|품목|수량|단가|금액|규격", joined, flags=re.IGNORECASE):
        return False
    return any(_official_section_from_text(row[0]) for row in non_empty_rows) or any(":" in row[0] or "：" in row[0] for row in non_empty_rows)


def _official_section_from_text(text: str) -> str | None:
    normalized = re.sub(r"\s+", "", str(text or "")).strip(":：")
    if normalized in {"공급자", "공급처"}:
        return "공급자"
    if normalized in {"공급받는자", "궁급받는자", "공급받는자정보", "고객사"}:
        return "공급받는자"
    return None


def _official_single_section_block(text: str) -> str | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    return _official_section_from_text(lines[0])


def _sectioned_official_key(section: str | None, key: str) -> str:
    if section and key in {"상호", "사업자번호", "담당", "대표자", "주소"}:
        return f"{section} {key}"
    return key


def _parse_official_key_value_text(text: str) -> list[tuple[str, str, int, int]]:
    known_label_pattern = (
        r"문서\s*번호|샘플\s*번호|사업자\s*번호|작성일|발행일|견적일|유효\s*기간|"
        r"납기일|요청일|담당|당당|상호|공급자|공급받는자|궁급받는자|입고창고|출고창고|요청부서|예상\s*합계|"
        r"합계\s*금액|총\s*합계|TOTAL(?:\s+[A-Z]+)?"
    )
    items: list[tuple[str, str, int, int]] = []
    colon_matches = list(re.finditer(rf"(?:(?<=^)|(?<=\s))({known_label_pattern})\s*[:：]\s*", text, flags=re.IGNORECASE))
    for index, match in enumerate(colon_matches):
        value_start = match.end()
        value_end = colon_matches[index + 1].start() if index + 1 < len(colon_matches) else len(text)
        key = _clean_official_key(match.group(1))
        value = _clean_cell(text[value_start:value_end])
        if _valid_official_key_value(key, value):
            items.append((key, value, match.start(), value_end))
    if items:
        return items
    match = re.match(rf"^\s*({known_label_pattern})\s+(.{{1,80}}?)\s*$", text, flags=re.IGNORECASE)
    if not match:
        return []
    key = _clean_official_key(match.group(1))
    value = _clean_cell(match.group(2))
    return [(key, value, match.start(1), match.end(2))] if _valid_official_key_value(key, value) else []


def _clean_official_key(value: str) -> str:
    key = re.sub(r"\s+", " ", str(value or "")).strip()
    aliases = {
        "문서 번호": "문서번호",
        "샘플 번호": "샘플번호",
        "사업자 번호": "사업자번호",
        "유효 기간": "유효기간",
        "예상 합계": "예상 합계",
        "당당": "담당",
    }
    compact = re.sub(r"\s+", "", key)
    return aliases.get(key) or aliases.get(compact) or key


def _valid_official_key_value(key: str, value: str) -> bool:
    if not key or not value:
        return False
    if len(key) > 40 or len(value) > 120:
        return False
    if key in {"문서유형", "제목", "통화"}:
        return False
    return True


def _normalize_official_block_bbox(
    block: dict[str, Any],
    *,
    width: int,
    height: int,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    full_width: int | None = None,
    full_height: int | None = None,
) -> list[float] | None:
    full_width = max(1, int(full_width or width))
    full_height = max(1, int(full_height or height))
    bbox = block.get("block_bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
            return _clamp_normalized_bbox([
                (origin_x + x1) / full_width,
                (origin_y + y1) / full_height,
                (origin_x + x2) / full_width,
                (origin_y + y2) / full_height,
            ])
        except (TypeError, ValueError):
            pass
    points = block.get("block_polygon_points")
    if isinstance(points, list) and points:
        try:
            xs = [float(point[0]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
            ys = [float(point[1]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
        except (TypeError, ValueError):
            return None
        if xs and ys:
            return _clamp_normalized_bbox([
                (origin_x + min(xs)) / full_width,
                (origin_y + min(ys)) / full_height,
                (origin_x + max(xs)) / full_width,
                (origin_y + max(ys)) / full_height,
            ])
    return None


def _slice_normalized_bbox_by_span(text: str, bbox: list[float] | None, start: int, end: int) -> list[float] | None:
    if not bbox:
        return None
    length = max(len(text), 1)
    span_start = max(0.0, min(1.0, start / length))
    span_end = max(span_start, min(1.0, end / length))
    width = bbox[2] - bbox[0]
    return _clamp_normalized_bbox([
        bbox[0] + width * span_start,
        bbox[1],
        bbox[0] + width * span_end,
        bbox[3],
    ])


def _slice_normalized_bbox_by_line(bbox: list[float] | None, index: int, count: int) -> list[float] | None:
    if not bbox:
        return None
    count = max(1, count)
    index = max(0, min(count - 1, index))
    height = bbox[3] - bbox[1]
    return _clamp_normalized_bbox([
        bbox[0],
        bbox[1] + height * (index / count),
        bbox[2],
        bbox[1] + height * ((index + 1) / count),
    ])


def _split_normalized_key_value_bbox(text: str, key: str, bbox: list[float] | None) -> tuple[list[float] | None, list[float] | None]:
    if not bbox:
        return None, None
    separator_index = max(text.find(":"), text.find("："))
    if separator_index < 0:
        separator_index = len(key)
    denominator = max(len(text), 1)
    split = bbox[0] + (bbox[2] - bbox[0]) * min(0.85, max(0.15, (separator_index + 1) / denominator))
    return _clamp_normalized_bbox([bbox[0], bbox[1], split, bbox[3]]), _clamp_normalized_bbox([split, bbox[1], bbox[2], bbox[3]])


def _clamp_normalized_bbox(bbox: list[float]) -> list[float]:
    return [round(max(0.0, min(1.0, float(value))), 6) for value in bbox]


def _dedupe_key_values(key_values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in key_values:
        identity = (str(item.get("key") or "").casefold(), str(item.get("value") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _official_table_quality(
    *,
    columns: list[str],
    raw_rows: list[list[str]],
    canonical_rows: list[dict[str, Any]],
    table_type: str,
    text: str,
    original_filename: str,
) -> dict[str, Any]:
    canonical_headers = [_canonical_field_for_header(column) for column in columns]
    non_empty_headers = [column for column in columns if _clean_cell(column)]
    mapped_headers = [header for header in canonical_headers if header]
    total_cells = sum(max(len(columns), len(row)) for row in raw_rows)
    empty_cells = 0
    for row in raw_rows:
        width = max(len(columns), len(row))
        for index in range(width):
            value = row[index] if index < len(row) else ""
            if not _clean_cell(value):
                empty_cells += 1
    empty_cell_ratio = (empty_cells / total_cells) if total_cells else 1.0
    header_match_score = (len(set(mapped_headers)) / len(non_empty_headers)) if non_empty_headers else 0.0
    row_boundary_quality = _official_table_row_boundary_quality(columns, raw_rows, canonical_rows)
    document_type = _official_table_document_type_for_quality(table_type, text, original_filename)
    expected_columns = _official_table_expected_column_groups(document_type)
    covered_expected: list[str] = []
    missing_expected: list[str] = []
    actual_fields = set(header for header in mapped_headers if header)
    for label, alternatives in expected_columns:
        if actual_fields.intersection(alternatives):
            covered_expected.append(label)
        else:
            missing_expected.append(label)
    expected_column_coverage = (len(covered_expected) / len(expected_columns)) if expected_columns else 0.0
    amount_fields = {"unit_price", "supply_amount", "tax_amount", "line_total"}
    amount_column_coverage = (
        len(actual_fields.intersection(amount_fields)) / len(amount_fields)
        if document_type in {"invoice", "transaction_statement", "purchase_order", "quotation"}
        else None
    )
    quality_score = (
        header_match_score * 0.30
        + row_boundary_quality * 0.30
        + expected_column_coverage * 0.30
        + max(0.0, 1.0 - empty_cell_ratio) * 0.10
    )
    warnings: list[str] = []
    if expected_column_coverage < 0.55:
        warnings.append("expected_column_coverage_low")
    if row_boundary_quality < 0.65:
        warnings.append("row_boundary_quality_low")
    if header_match_score < 0.45:
        warnings.append("header_match_score_low")
    return {
        "version": 1,
        "table_count": 1,
        "document_type_guess": document_type,
        "table_type": table_type,
        "column_count": len(columns),
        "row_count": len(canonical_rows),
        "raw_row_count": len(raw_rows),
        "empty_cell_ratio": round(empty_cell_ratio, 4),
        "header_match_score": round(header_match_score, 4),
        "row_boundary_quality": round(row_boundary_quality, 4),
        "expected_column_coverage": round(expected_column_coverage, 4),
        "amount_column_coverage": round(amount_column_coverage, 4) if amount_column_coverage is not None else None,
        "quality_score": round(max(0.0, min(1.0, quality_score)), 4),
        "covered_expected_columns": covered_expected,
        "missing_expected_columns": missing_expected,
        "mapped_headers": [header for header in canonical_headers if header],
        "warnings": warnings,
    }


def _official_table_row_boundary_quality(
    columns: list[str],
    raw_rows: list[list[str]],
    canonical_rows: list[dict[str, Any]],
) -> float:
    if not raw_rows:
        return 0.0
    expected_width = max(1, len(columns))
    width_scores: list[float] = []
    content_scores: list[float] = []
    for row in raw_rows:
        width_delta = abs(len(row) - expected_width)
        width_scores.append(max(0.0, 1.0 - (width_delta / expected_width)))
        non_empty = sum(1 for cell in row if _clean_cell(cell))
        content_scores.append(min(1.0, non_empty / max(1, min(expected_width, 3))))
    retained_ratio = len(canonical_rows) / len(raw_rows)
    return max(0.0, min(1.0, (sum(width_scores) / len(width_scores)) * 0.45 + (sum(content_scores) / len(content_scores)) * 0.35 + retained_ratio * 0.20))


def _official_table_document_type_for_quality(table_type: str, text: str, original_filename: str) -> str:
    if table_type == "incoming_inspection":
        return "inspection_report"
    haystack = f"{original_filename}\n{text}".casefold()
    if re.search(r"검사|inspection|iqc", haystack):
        return "inspection_report"
    if re.search(r"납품서|delivery", haystack):
        return "delivery_note"
    if re.search(r"발주서|purchase\s*order|\bpo[-_]", haystack):
        return "purchase_order"
    if re.search(r"견적|quotation|quote", haystack):
        return "quotation"
    if re.search(r"거래명세|transaction", haystack):
        return "transaction_statement"
    if re.search(r"세금계산서|invoice|commercial", haystack):
        return "invoice"
    return "line_items"


def _official_table_expected_column_groups(document_type: str) -> list[tuple[str, set[str]]]:
    return expected_column_groups(document_type)


def _official_parsing_blocks(output: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    items = output if isinstance(output, (list, tuple)) else [output]
    for item in items or []:
        for payload in _official_json_payload_candidates(item):
            blocks.extend(_collect_official_parsing_blocks(payload))
    return blocks


def _official_json_payload_candidates(item: Any) -> list[Any]:
    candidates: list[Any] = []

    # PaddleOCRVLResult is dict-like, but the official structured payload lives
    # under its `.json["res"]` property.  Read that first so the worker does not
    # mistake the result wrapper itself for the final JSON payload.
    for attr in ("json",):
        if not hasattr(item, attr):
            continue
        value = getattr(item, attr)
        try:
            value = value() if callable(value) else value
        except TypeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.append(value)
        elif isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                candidates.append(json.loads(value))
            except Exception:
                pass
    if isinstance(item, Mapping):
        candidates.append(dict(item))
        json_value = item.get("json")
        if isinstance(json_value, (dict, list)):
            candidates.append(json_value)
        res_value = item.get("res")
        if isinstance(res_value, dict):
            candidates.append({"res": res_value})
    for attr in ("str",):
        if not hasattr(item, attr):
            continue
        value = getattr(item, attr)
        try:
            value = value() if callable(value) else value
        except TypeError:
            continue
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                candidates.append(json.loads(value))
            except Exception:
                pass
    if not candidates:
        candidates.extend(_official_json_payloads_from_save_to_json(item))
    return candidates


def _official_json_payloads_from_save_to_json(item: Any) -> list[Any]:
    method = getattr(item, "save_to_json", None)
    if not callable(method):
        return []
    payloads: list[Any] = []
    try:
        with tempfile.TemporaryDirectory(prefix="docparse_paddleocrvl_json_") as tmp:
            tmp_path = Path(tmp)
            try:
                signature = inspect.signature(method)
                parameters = signature.parameters
                if "save_path" in parameters:
                    method(save_path=str(tmp_path))
                elif "save_dir" in parameters:
                    method(save_dir=str(tmp_path))
                else:
                    method(str(tmp_path))
            except TypeError:
                try:
                    method(str(tmp_path))
                except TypeError:
                    return []
            for json_path in tmp_path.rglob("*.json"):
                try:
                    payloads.append(json.loads(json_path.read_text(encoding="utf-8")))
                except Exception:
                    continue
    except Exception:
        return []
    return payloads


def _collect_official_parsing_blocks(payload: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            blocks.extend(_collect_official_parsing_blocks(item))
        return blocks
    if not isinstance(payload, Mapping):
        return blocks
    parsing = payload.get("parsing_res_list")
    if isinstance(parsing, list):
        blocks.extend(block for block in parsing if isinstance(block, dict))
    for key in ("res", "result", "results", "page_res_list", "pages", "data"):
        nested = payload.get(key)
        if nested is not None:
            blocks.extend(_collect_official_parsing_blocks(nested))
    return blocks


def _parse_html_table_rows(html_table: str) -> list[list[str]]:
    parser = _HTMLTableParser()
    try:
        parser.feed(html_table)
        parser.close()
    except Exception:
        return []
    return parser.rows


def _guess_official_table_type(
    columns: list[str],
    rows: list[list[str]],
    text: str,
    original_filename: str,
) -> str:
    haystack = "\n".join([original_filename, text, " ".join(columns), *(" ".join(row) for row in rows[:3])])
    if _looks_like_incoming_inspection(haystack, original_filename):
        return "incoming_inspection"
    if re.search(r"(입고수량|검사항목|판정|Lot\s*/?\s*Code)", haystack, flags=re.IGNORECASE):
        return "incoming_inspection"
    return "line_items"


def _canonical_row_from_official_table(columns: list[str], raw_row: list[str], table_type: str) -> dict[str, Any]:
    return canonicalize_official_table_row(columns, raw_row, table_type)


def _canonical_field_for_header(header: str) -> str | None:
    return canonical_field_for_header(header)


def _split_official_inspection_item_fields(row: dict[str, Any]) -> dict[str, Any]:
    item_name = _clean_cell(str(row.get("item_name") or ""))
    if not item_name:
        return {}
    if row.get("document_item_code") and row.get("specification"):
        return {}
    tokens = item_name.split()
    if len(tokens) < 2:
        return {}
    code_index: int | None = None
    spec_index: int | None = None
    for index, token in enumerate(tokens):
        if not row.get("document_item_code") and re.fullmatch(r"[A-Z]{2,8}(?:[-_][A-Z0-9]{1,12})+", token, flags=re.IGNORECASE):
            code_index = index
        if not row.get("specification") and re.fullmatch(r"(?:M\d+(?:[xX]\d+)?|\d+[xX]\d+(?:[xX]\d+)?|\d+(?:mm|T|P)|\d+P)", token, flags=re.IGNORECASE):
            spec_index = index
    updates: dict[str, Any] = {}
    cut_indexes = [index for index in (code_index, spec_index) if index is not None]
    if code_index is not None:
        updates["document_item_code"] = tokens[code_index]
    if spec_index is not None:
        updates["specification"] = tokens[spec_index]
    if cut_indexes:
        cut = min(cut_indexes)
        cleaned_name = " ".join(tokens[:cut]).strip()
        if cleaned_name:
            updates["item_name"] = cleaned_name
    return updates


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


def _tables_from_schema_payload(payload: dict[str, Any], *, source: str = "vl_schema_prompt") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_type = str(table.get("table_type") or "unknown").strip() or "unknown"
        rows = table.get("rows") if isinstance(table.get("rows"), list) else []
        normalized_rows = (
            [_normalize_schema_inspection_row(row) for row in rows if isinstance(row, dict)]
            if table_type == "incoming_inspection"
            else [dict(row) for row in rows if isinstance(row, dict)]
        )
        normalized_rows = [row for row in normalized_rows if row]
        if not normalized_rows:
            continue
        warnings = sorted(set([*(table.get("warnings") or []), "vl_schema_prompt_table_review_required"]))
        if table_type == "incoming_inspection":
            warnings = sorted(set([*warnings, "inspection_report_no_amount_fields"]))
        tables.append(
            {
                "table_type": table_type,
                "source": source,
                "schema_version": VLM_STRUCTURED_OUTPUT_SCHEMA["version"],
                "columns": table.get("columns") or VLM_STRUCTURED_OUTPUT_SCHEMA["table_types"].get(table_type, {}).get("columns", []),
                "rows": normalized_rows,
                "warnings": warnings,
                "review_required": True,
                "amount_fields_policy": "null_for_inspection_report" if table_type == "incoming_inspection" else table.get("amount_fields_policy"),
            }
        )
    return tables


def _key_values_from_schema_payload(payload: dict[str, Any], *, source: str = "vl_schema_prompt") -> list[dict[str, Any]]:
    key_values: list[dict[str, Any]] = []
    values = payload.get("key_values") if isinstance(payload.get("key_values"), list) else []
    for item in values:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("label") or item.get("field") or item.get("name")
        value = item.get("value") if item.get("value") is not None else item.get("normalized_value")
        if key in (None, "") or value in (None, ""):
            continue
        next_item: dict[str, Any] = {
            "key": str(key),
            "value": value,
            "source": "vl_direct_key_value_bbox",
            "vl_source": source,
        }
        for field in ("bbox", "normalized_bbox", "key_bbox", "value_bbox", "page_index", "page", "confidence"):
            field_value = item.get(field)
            if field_value not in (None, "", []):
                next_item[field] = field_value
        key_values.append(next_item)
    return key_values


def _normalize_schema_inspection_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in ("no", "received_quantity", "accepted_quantity", "defective_quantity"):
        value = row.get(field)
        if value not in (None, ""):
            normalized[field] = _int_text(str(value))
    for field in (
        "item_name",
        "lot_code",
        "document_item_code",
        "specification",
        "inspection_item",
        "result",
        "note",
    ):
        value = _clean_cell(str(row.get(field) or ""))
        if value:
            normalized[field] = value
    if "result" in normalized:
        normalized["result"] = re.sub(r"조건부\s*합격|조건부합격", "조건부 합격", normalized["result"]).strip()
    for amount_field in ("unit_price", "supply_amount", "tax_amount", "line_total", "subtotal", "total", "currency"):
        normalized.pop(amount_field, None)
    flags = list(row.get("review_flags") or [])
    flags.extend(_inspection_uncertainty_warnings(" ".join(str(value) for value in row.values()), normalized))
    flags.append("vl_schema_prompt_inspection_review_required")
    normalized["review_flags"] = sorted(set(flag for flag in flags if flag))
    return normalized if normalized.get("item_name") else {}


def _extract_structured_tables(text: str, output: Any, *, original_filename: str = "") -> list[dict[str, Any]]:
    """Build review-only JSON tables from VL output.

    PaddleOCRVL's current Python surface returns layout/text artifacts rather
    than accepting a custom JSON schema prompt.  This worker still exposes the
    schema contract at the boundary and converts the VLM-visible table text into
    structured rows before it reaches the backend parser.
    """

    candidate_text = "\n".join(_candidate_table_texts(output, text))
    if not _looks_like_incoming_inspection(candidate_text, original_filename):
        return []
    rows, warnings = _extract_incoming_inspection_rows(candidate_text)
    if not rows:
        return []
    return [
        {
            "table_type": "incoming_inspection",
            "source": "vl_worker_table_extractor",
            "schema_version": VLM_STRUCTURED_OUTPUT_SCHEMA["version"],
            "columns": VLM_STRUCTURED_OUTPUT_SCHEMA["table_types"]["incoming_inspection"]["columns"],
            "rows": rows,
            "warnings": sorted(set(warnings + ["vl_table_review_required", "inspection_report_no_amount_fields"])),
            "review_required": True,
            "amount_fields_policy": "null_for_inspection_report",
        }
    ]


def _candidate_table_texts(output: Any, text: str) -> list[str]:
    fragments: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("block_content", "rec_text", "text", "content", "markdown", "html"):
                content = value.get(key)
                if isinstance(content, str) and _table_like_text(content):
                    fragments.append(content)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str) and _table_like_text(value):
            fragments.append(value)

    walk(output)
    if text:
        fragments.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        normalized = re.sub(r"\s+", " ", fragment).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(fragment)
    return deduped or [text]


def _table_like_text(value: str) -> bool:
    return bool(
        re.search(r"(입고\s*검사|검사\s*기록|검사\s*성적|inspection)", value, flags=re.IGNORECASE)
        or re.search(r"(입고수량|합격수량|불량수량|판정|Lot\s*No)", value, flags=re.IGNORECASE)
    )


def _looks_like_incoming_inspection(text: str, filename: str = "") -> bool:
    haystack = f"{filename}\n{text}"
    return bool(
        re.search(r"(incoming[_ -]?inspection|입고\s*검사|검사\s*기록|검사\s*성적|검사번호)", haystack, flags=re.IGNORECASE)
        and re.search(r"(입고수량|합격수량|불량수량|판정|합격|불량)", haystack, flags=re.IGNORECASE)
    )


def _extract_incoming_inspection_rows(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for line in _normalized_table_lines(text):
        if _inspection_header_or_note(line):
            continue
        row = _parse_incoming_inspection_line(line)
        if not row:
            continue
        row_warnings = _inspection_uncertainty_warnings(line, row)
        if row_warnings:
            row["review_flags"] = sorted(set([*(row.get("review_flags") or []), *row_warnings]))
            warnings.extend(row_warnings)
        rows.append(row)
    return _dedupe_inspection_rows(rows), sorted(set(warnings))


def _normalized_table_lines(text: str) -> list[str]:
    normalized = str(text or "")
    normalized = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    lines: list[str] = []
    for raw in normalized.splitlines():
        line = raw.strip().strip("|")
        if not line:
            continue
        line = re.sub(r"\s*\|\s*", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def _inspection_header_or_note(line: str) -> bool:
    return inspection_header_or_note(line)


def _parse_incoming_inspection_line(line: str) -> dict[str, Any] | None:
    parsed = _split_inspection_row_cells(line)
    if not parsed:
        return None
    item_name, specification, lot_code, document_item_code = _split_inspection_identity(parsed["prefix"])
    if not item_name:
        return None
    inspection_item, result, note = _split_inspection_tail(parsed.get("tail") or "")
    row: dict[str, Any] = {
        "no": parsed["no"],
        "item_name": item_name,
        "received_quantity": parsed["received_quantity"],
        "review_flags": ["vl_table_structured_inspection_review_required"],
    }
    if parsed.get("accepted_quantity") is not None:
        row["accepted_quantity"] = parsed["accepted_quantity"]
    if parsed.get("defective_quantity") is not None:
        row["defective_quantity"] = parsed["defective_quantity"]
    if specification:
        row["specification"] = specification
    if lot_code:
        row["lot_code"] = lot_code
    if document_item_code:
        row["document_item_code"] = document_item_code
    if inspection_item:
        row["inspection_item"] = inspection_item
    if result:
        row["result"] = result
    if note:
        row["note"] = note
    return row


def _split_inspection_row_cells(line: str) -> dict[str, Any] | None:
    tokens = [token for token in str(line or "").split() if token]
    if len(tokens) < 5 or not re.fullmatch(r"\d{1,3}", tokens[0]):
        return None
    quantity_indexes = [
        index for index, token in enumerate(tokens[1:], start=1)
        if re.fullmatch(r"\d{1,6}(?:,\d{3})?", token)
    ]
    if not quantity_indexes:
        return None
    quantity_index = quantity_indexes[0]
    prefix_tokens = tokens[1:quantity_index]
    if not prefix_tokens:
        return None
    received = _int_text(tokens[quantity_index])
    accepted: int | None = None
    defective: int | None = None
    tail_start = quantity_index + 1
    if (
        tail_start + 1 < len(tokens)
        and re.fullmatch(r"\d{1,6}(?:,\d{3})?", tokens[tail_start])
        and re.fullmatch(r"\d{1,6}(?:,\d{3})?", tokens[tail_start + 1])
    ):
        accepted = _int_text(tokens[tail_start])
        defective = _int_text(tokens[tail_start + 1])
        tail_start += 2
    return {
        "no": _int_text(tokens[0]),
        "prefix": " ".join(prefix_tokens),
        "received_quantity": received,
        "accepted_quantity": accepted,
        "defective_quantity": defective,
        "tail": " ".join(tokens[tail_start:]).strip(),
    }


def _split_inspection_identity(prefix: str) -> tuple[str | None, str | None, str | None, str | None]:
    tokens = [token for token in str(prefix or "").split() if token]
    if not tokens:
        return None, None, None, None
    lot_code: str | None = None
    document_item_code: str | None = None
    filtered: list[str] = []
    for token in tokens:
        if re.fullmatch(r"(?:LOT|L)[-_]?[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*", token, flags=re.IGNORECASE):
            lot_code = token
            continue
        if re.fullmatch(
            r"(?:[A-Z]{1,6}[-_])?\d+[xX]\d+(?:[xX]\d+)?|M\d+(?:[xX]\d+)?|BH-\d+|\d+(?:mm|T|P)|[A-Z]{1,5}-\d+",
            token,
            flags=re.IGNORECASE,
        ):
            filtered.append(token)
            continue
        if re.fullmatch(
            r"(?:IQC|QC|INS)[-_]?[A-Za-z0-9]+|[A-Z]{2,8}(?:[-_][A-Z0-9]{2,8})+",
            token,
            flags=re.IGNORECASE,
        ):
            document_item_code = token
            continue
        filtered.append(token)
    spec_index: int | None = None
    for index, token in enumerate(filtered):
        if re.fullmatch(
            r"(?:[A-Z]{1,6}[-_])?\d+[xX]\d+(?:[xX]\d+)?|M\d+(?:[xX]\d+)?|BH-\d+|\d+(?:mm|T|P)|[A-Z]{1,5}-\d+",
            token,
            flags=re.IGNORECASE,
        ):
            spec_index = index
    if spec_index is None:
        return _clean_cell(" ".join(filtered)), None, lot_code, document_item_code
    return (
        _clean_cell(" ".join(filtered[:spec_index])),
        _clean_cell(" ".join(filtered[spec_index:])),
        lot_code,
        document_item_code,
    )


def _split_inspection_tail(tail: str) -> tuple[str | None, str | None, str | None]:
    if not tail:
        return None, None, None
    result_match = re.search(r"(조건부\s*합격|조건부합격|불합격|재검|보류|합격)", tail)
    result = None
    inspection_item = tail
    if result_match:
        result = re.sub(r"조건부\s*합격", "조건부 합격", result_match.group(1)).strip()
        inspection_item = tail[: result_match.start()].strip(" -:：")
        note = tail[result_match.end() :].strip(" -:：")
    else:
        note = None
    return inspection_item or None, result, note or None


def _inspection_uncertainty_warnings(line: str, row: dict[str, Any]) -> list[str]:
    joined = " ".join(str(value) for value in row.values() if value not in (None, "", []))
    source = f"{line} {joined}"
    warnings: list[str] = []
    if re.search(r"(?<=\d)[Oo](?=\d)|(?<![A-Za-z])[Oo](?=\d)", source):
        warnings.append("ocr_o_zero_uncertain")
    if re.search(r"\(?주\)?|㈜", source):
        warnings.append("company_marker_uncertain")
    if re.search(r"(유동|유통|검사|경사)", source):
        warnings.append("ocr_similar_word_requires_review")
    return warnings


def _int_text(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", ""))
    except Exception:
        return None


def _clean_cell(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -:：")
    return text or None


def _dedupe_inspection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = (
            row.get("no"),
            row.get("item_name"),
            row.get("specification"),
            row.get("received_quantity"),
            row.get("accepted_quantity"),
            row.get("defective_quantity"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped
