from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.document import DocumentType
from app.services.file_ingestion import NormalizedDocument
from app.services.parser import ParsedDocument
from app.services.quality_evaluation import QualityEvaluation


MANUFACTURING_TYPES = {
    DocumentType.purchase_order,
    DocumentType.quotation,
    DocumentType.transaction_statement,
    DocumentType.delivery_note,
    DocumentType.invoice,
    DocumentType.packing_list,
}


@dataclass(frozen=True)
class AIEscalationDecision:
    should_escalate: bool
    reasons: list[str] = field(default_factory=list)
    quality_signals: dict[str, Any] = field(default_factory=dict)


def should_escalate_to_ai(
    normalized: NormalizedDocument,
    parsed: ParsedDocument,
    extraction_quality: QualityEvaluation | None = None,
) -> AIEscalationDecision:
    """Decide whether deterministic extraction needs AI correction."""
    reasons: list[str] = []
    signals: dict[str, Any] = {
        "source_file_type": normalized.source_file_type,
        "extraction_method": normalized.extraction_method,
        "ocr_confidence": normalized.ocr_confidence,
        "line_item_count": len(parsed.line_items or []),
    }
    method = (normalized.extraction_method or "").lower()
    source = (normalized.source_file_type or "").lower()

    if normalized.partial_support:
        reasons.append("partial_file_support")
    if normalized.extraction_warnings:
        reasons.append("extraction_warnings_present")
    if normalized.ocr_confidence is not None and normalized.ocr_confidence < 0.68:
        reasons.append("low_ocr_confidence")
    if extraction_quality:
        signals["extraction_quality_score"] = extraction_quality.score
        signals["extraction_quality_sufficient"] = extraction_quality.sufficient
        if extraction_quality.escalation_recommended:
            reasons.append("quality_gate_escalation_recommended")
        if extraction_quality.score < 0.72:
            reasons.append("low_extraction_quality_score")

    table_confidence = _metadata_number(normalized.file_metadata, "table_confidence")
    if table_confidence is not None:
        signals["table_confidence"] = table_confidence
        if table_confidence < 0.70:
            reasons.append("low_table_confidence")
    if "pdf_scanned" in method or "ocr" in method or source in {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp"}:
        signals["ocr_or_image_path"] = True
    if "pdf_partial" in method:
        reasons.append("pdf_partial_text")

    if parsed.document_type in MANUFACTURING_TYPES:
        reasons.extend(_manufacturing_missing_required(parsed))
        reasons.extend(_line_item_quality_reasons(parsed))
        if _amount_mismatch(parsed):
            reasons.append("amount_mismatch")
    elif parsed.document_type == DocumentType.other and (normalized.primary_image_path or normalized.heavy_ai_candidate):
        reasons.append("unknown_document_type_from_visual_source")

    blocking_reasons = {
        "partial_file_support",
        "extraction_warnings_present",
        "low_ocr_confidence",
        "quality_gate_escalation_recommended",
        "low_extraction_quality_score",
        "low_table_confidence",
        "pdf_partial_text",
        "missing_required_header",
        "missing_line_items",
        "incomplete_line_items",
        "amount_mismatch",
        "unknown_document_type_from_visual_source",
    }
    should_escalate = any(reason in blocking_reasons for reason in reasons)
    return AIEscalationDecision(
        should_escalate=should_escalate,
        reasons=list(dict.fromkeys(reasons)),
        quality_signals=signals,
    )


def _manufacturing_missing_required(parsed: ParsedDocument) -> list[str]:
    missing = []
    if not (parsed.vendor_name or parsed.merchant_name):
        missing.append("missing_required_header")
    if not parsed.customer_name:
        missing.append("missing_required_header")
    if not parsed.document_number:
        missing.append("missing_required_header")
    if not (parsed.issue_date or parsed.extracted_date):
        missing.append("missing_required_header")
    if parsed.document_type == DocumentType.purchase_order and not parsed.due_date:
        missing.append("missing_required_header")
    if parsed.document_type == DocumentType.invoice and not parsed.due_date:
        missing.append("missing_required_header")
    return missing


def _line_item_quality_reasons(parsed: ParsedDocument) -> list[str]:
    if not parsed.line_items:
        return ["missing_line_items"]
    incomplete = False
    for item in parsed.line_items:
        if item.get("item_name") in (None, "", []):
            incomplete = True
        if item.get("quantity") in (None, "", []):
            incomplete = True
        if parsed.document_type != DocumentType.delivery_note and item.get("unit_price") in (None, "", []) and item.get("line_total") in (None, "", []):
            incomplete = True
        if item.get("validation_warnings"):
            incomplete = True
    return ["incomplete_line_items"] if incomplete else []


def _amount_mismatch(parsed: ParsedDocument) -> bool:
    expected = _decimal(parsed.extracted_amount)
    if expected is None or not parsed.line_items:
        return False
    total = Decimal("0")
    counted = 0
    for item in parsed.line_items:
        value = _decimal(item.get("line_total"))
        if value is not None:
            total += value
            counted += 1
    if counted == 0:
        return False
    tolerance = Decimal("10") if (parsed.currency or "KRW") == "KRW" else Decimal("0.05")
    return abs(expected - total) > tolerance


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", []):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("₩", "").replace("원", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _metadata_number(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    if value is None:
        nested = metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {}
        value = nested.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
