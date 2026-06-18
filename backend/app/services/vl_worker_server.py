from __future__ import annotations

import base64
import inspect
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel
import requests

from app.core.config import get_settings
from app.scripts.smoke_paddleocr_vl_gguf import build_docuparse_vl_candidate_metadata, extract_text, validate_output_text


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
    "response_fields": ["raw_text", "tables"],
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
        schema_metadata: dict[str, Any] = {"enabled": bool(getattr(settings, "paddleocr_vl_gguf_schema_prompt_enabled", False))}
        schema_payload: dict[str, Any] | None = None
        tables: list[dict[str, Any]] = []
        output: Any
        if schema_metadata["enabled"]:
            schema_payload, schema_metadata = _run_schema_prompt_inference(image_path, settings)
        if schema_payload:
            text = str(schema_payload.get("raw_text") or "")
            tables = _tables_from_schema_payload(schema_payload, source=schema_metadata.get("table_source") or "vl_schema_prompt")
            output = [{"text": text, "structured_json": schema_payload}]
        else:
            with _inference_lock:
                output = _predict_with_optional_paddle_schema_prompt(image_path, settings)
            text = extract_text(output)
            schema_json = _schema_json_from_output(output)
            if schema_json:
                schema_metadata.update({"used": True, "transport": "paddleocr_predict_prompt"})
                text = str(schema_json.get("raw_text") or text)
                tables = _tables_from_schema_payload(schema_json, source="vl_schema_prompt")
            elif schema_metadata.get("attempted") and text.strip():
                schema_metadata["repair_attempted"] = True
                repaired, repair_error = _run_llama_schema_json_repair(text, settings)
                if repaired and _tables_from_schema_payload(repaired, source="vl_schema_prompt_repair"):
                    schema_metadata.update(
                        {
                            "used": True,
                            "transport": "paddleocr_predict_text_schema_repair",
                            "repair_used": True,
                            "table_source": "vl_schema_prompt_repair",
                        }
                    )
                    text = str(repaired.get("raw_text") or text)
                    tables = _tables_from_schema_payload(repaired, source="vl_schema_prompt_repair")
                    output = [{"text": text, "structured_json": repaired, "source_output": output}]
                else:
                    schema_metadata["repair_error"] = repair_error
        validation = validate_output_text(text, [])
        if not tables:
            tables = _extract_structured_tables(text, output, original_filename=original_filename or path.name)
        if validation.get("ok"):
            _last_error = None
            _last_success_at = _utc_now_iso()
        report.update(
            {
                "ok": bool(validation.get("ok")),
                "classification": validation.get("status"),
                "validation": validation,
                "render": {"image_path": str(image_path)},
                "text_preview": text[:5000],
                "structured_schema": VLM_STRUCTURED_OUTPUT_SCHEMA,
                "schema_prompt": schema_metadata,
                "tables": tables,
                "provider_available_candidate": bool(validation.get("ok")),
                "provider_available_decision_reason": "vl_worker_output_readable" if validation.get("ok") else "vl_worker_output_invalid",
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
    if getattr(settings, "paddleocr_vl_gguf_schema_prompt_enabled", False):
        signature = inspect.signature(pipeline.predict)
        prompt_key = next(
            (
                key
                for key in ("prompt", "instruction", "query", "task_prompt")
                if key in signature.parameters
            ),
            None,
        )
        if prompt_key:
            kwargs[prompt_key] = VLM_TABLE_EXTRACTION_PROMPT
    return pipeline.predict(str(image_path), **kwargs)


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
        if parsed and isinstance(parsed.get("tables"), list):
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
    compact = re.sub(r"\s+", "", line)
    if re.search(r"^(No|번호)?품목(?:명)?규격입고수량합격(?:수량)?불량(?:수량)?판정", compact, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(r"(검사의견|금액항목없음|금액정보없음|문서번호|검사일|협력사|검사자|품질팀)", compact)
        and not re.match(r"^\d{1,3}\s+", line)
    )


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
