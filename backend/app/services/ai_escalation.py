from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.document import DocumentType
from app.services.file_ingestion import NormalizedDocument
from app.services.ocr_table_reconstructor import reconstruct_ocr_line_items
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
    severity: str = "info"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def quality_signals(self) -> dict[str, Any]:
        return self.signals


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
    visual_or_ocr_source = (
        normalized.heavy_ai_candidate
        or normalized.partial_support
        or normalized.primary_image_path is not None
        or "ocr" in method
        or "pdf_partial" in method
        or source in {"png", "jpg", "jpeg", "tif", "tiff", "webp"}
    )

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
        if extraction_quality.score < 0.58 or (visual_or_ocr_source and extraction_quality.score < 0.72):
            reasons.append("low_extraction_quality_score")

    table_confidence = _metadata_number(normalized.file_metadata, "table_confidence")
    if table_confidence is not None:
        signals["table_confidence"] = table_confidence
        if table_confidence < 0.70:
            reasons.append("low_table_confidence")
    if "pdf_scanned" in method or "ocr" in method or source in {"png", "jpg", "jpeg", "tif", "tiff", "webp"}:
        signals["ocr_or_image_path"] = True
        if not parsed.line_items:
            ocr_candidates = reconstruct_ocr_line_items((normalized.normalized_text or "").splitlines())
            if ocr_candidates:
                signals["ocr_line_item_candidate_count"] = len(ocr_candidates)
                reasons.append("ocr_line_item_candidates_not_parsed")
    if "pdf_partial" in method:
        reasons.append("pdf_partial_text")

    if parsed.document_type in MANUFACTURING_TYPES:
        missing_required = _manufacturing_missing_required(parsed)
        if missing_required:
            signals["missing_required_fields"] = missing_required
            reasons.append("missing_required_fields")
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
        "missing_required_fields",
        "missing_required_header",
        "missing_line_items",
        "incomplete_line_items",
        "amount_mismatch",
        "unknown_document_type_from_visual_source",
        "ocr_line_item_candidates_not_parsed",
    }
    should_escalate = any(reason in blocking_reasons for reason in reasons)
    severity = "warning" if should_escalate else "info"
    confidence = _decision_confidence(signals, reasons)
    return AIEscalationDecision(
        should_escalate=should_escalate,
        severity=severity,
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        signals=signals,
    )


def _manufacturing_missing_required(parsed: ParsedDocument) -> list[str]:
    missing = []
    if not (parsed.vendor_name or parsed.merchant_name):
        missing.append("vendor_name")
    if not parsed.customer_name:
        missing.append("customer_name")
    if not parsed.document_number:
        missing.append("document_number")
    if not (parsed.issue_date or parsed.extracted_date):
        missing.append("issue_date")
    if parsed.document_type == DocumentType.purchase_order and not parsed.due_date:
        missing.append("due_date")
    if parsed.document_type == DocumentType.invoice and not parsed.due_date:
        missing.append("payment_due_date")
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


def _decision_confidence(signals: dict[str, Any], reasons: list[str]) -> float:
    score = 0.55
    if signals.get("extraction_quality_score") is not None:
        score = max(score, 1.0 - float(signals["extraction_quality_score"]))
    if signals.get("ocr_confidence") is not None:
        score = max(score, 1.0 - float(signals["ocr_confidence"]))
    if signals.get("table_confidence") is not None:
        score = max(score, 1.0 - float(signals["table_confidence"]))
    if "missing_required_fields" in reasons:
        score = max(score, 0.78)
    if "incomplete_line_items" in reasons or "missing_line_items" in reasons:
        score = max(score, 0.82)
    if "amount_mismatch" in reasons:
        score = max(score, 0.86)
    return round(min(0.99, max(0.0, score if reasons else 0.15)), 3)
