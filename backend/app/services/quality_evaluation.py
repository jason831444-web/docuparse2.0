import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.document import Document, DocumentType
from app.services.ai_document_understanding import AIDocumentUnderstandingResult
from app.services.file_ingestion import NormalizedDocument
from app.services.parser import ParsedDocument


MANUFACTURING_TYPES = {
    DocumentType.purchase_order,
    DocumentType.quotation,
    DocumentType.transaction_statement,
    DocumentType.delivery_note,
    DocumentType.invoice,
    DocumentType.packing_list,
}


@dataclass
class QualityEvaluation:
    stage: str
    score: float
    sufficient: bool
    review_required: bool
    escalation_recommended: bool = False
    reasons: list[str] = field(default_factory=list)


class DocumentQualityEvaluator:
    """Quality gates for deciding escalation and review status."""

    def evaluate_extraction(self, normalized: NormalizedDocument, parsed: ParsedDocument) -> QualityEvaluation:
        reasons: list[str] = []
        score = 0.45
        text = normalized.normalized_text or ""
        lines = [line for line in text.splitlines() if line.strip()]
        line_count = len(lines)
        lowered = text.lower()

        if len(text.strip()) >= 120:
            score += 0.18
        elif len(text.strip()) < 40:
            reasons.append("Very little text was extracted.")
            score -= 0.18

        if line_count >= 4:
            score += 0.08
        if normalized.ocr_confidence is not None:
            if normalized.ocr_confidence >= 0.75:
                score += 0.16
            elif normalized.ocr_confidence < 0.55:
                reasons.append("OCR confidence is low.")
                score -= 0.16

        if normalized.partial_support:
            reasons.append("File format support is partial.")
            score -= 0.25

        if normalized.extraction_warnings:
            reasons.extend(normalized.extraction_warnings)
            score -= min(0.18, len(normalized.extraction_warnings) * 0.06)

        if parsed.extracted_date:
            score += 0.05
        if parsed.document_type in MANUFACTURING_TYPES:
            if parsed.document_number:
                score += 0.05
            if parsed.vendor_name or parsed.merchant_name:
                score += 0.05
            if parsed.line_items:
                score += 0.12
            else:
                reasons.append("No manufacturing line items were extracted.")
                score -= 0.18
            incomplete_items = self._incomplete_line_items(parsed.line_items)
            if incomplete_items:
                reasons.append("Some line items are missing quantity, unit price, or line total.")
                score -= 0.12
        if parsed.document_type == DocumentType.receipt and parsed.extracted_amount:
            score += 0.08
            merchant_present = bool(parsed.merchant_name)
            amount_pattern_count = len(re.findall(r"\b\d{1,6}(?:,\d{3})*\.\d{2}\b", text))
            receipt_keywords = sum(keyword in lowered for keyword in ["total", "subtotal", "tax", "receipt", "change", "visa"])
            noise_lines = sum(1 for line in lines if self._is_noisy_line(line))
            noise_ratio = (noise_lines / line_count) if line_count else 0.0
            receipt_complete = merchant_present and parsed.extracted_date is not None and parsed.extracted_amount is not None
            if receipt_complete:
                score += 0.10
            if amount_pattern_count >= 3:
                score += 0.05
            if receipt_keywords >= 3:
                score += 0.05
            if noise_ratio > 0.35:
                reasons.append("Receipt text looks noisy.")
                score -= 0.10

        score = self._clamp(score)
        escalation = bool(normalized.primary_image_path and (score < 0.72 or parsed.document_type == DocumentType.other))
        if parsed.document_type == DocumentType.receipt:
            receipt_complete = bool(parsed.merchant_name and parsed.extracted_date and parsed.extracted_amount)
            if receipt_complete and score >= 0.72:
                escalation = False
                reasons.append("Receipt fields are already usable without heavy vision extraction.")
        sufficient = score >= 0.58 and bool(text.strip())
        return QualityEvaluation(
            stage="post_ingestion",
            score=score,
            sufficient=sufficient,
            review_required=score < 0.62 or normalized.partial_support or (parsed.document_type in MANUFACTURING_TYPES and (not parsed.line_items or self._incomplete_line_items(parsed.line_items))),
            escalation_recommended=escalation,
            reasons=reasons or ["Extracted content passed the first quality gate."],
        )

    def evaluate_structured_result(
        self,
        document: Document,
        ai_result: AIDocumentUnderstandingResult,
        extraction_quality: QualityEvaluation,
    ) -> QualityEvaluation:
        reasons: list[str] = []
        score = float(ai_result.confidence_score or Decimal("0.70"))

        if ai_result.review_required:
            reasons.append("Extractor requested manual review.")
            score -= 0.08
        if ai_result.extraction_notes:
            reasons.extend(ai_result.extraction_notes[:4])

        if ai_result.document_type in MANUFACTURING_TYPES:
            if not ai_result.document_number:
                reasons.append("Document number is missing.")
                score -= 0.06
            if not (ai_result.vendor_name or ai_result.merchant_name):
                reasons.append("Vendor or supplier name is missing.")
                score -= 0.08
            if not ai_result.line_items:
                reasons.append("Line items are missing.")
                score -= 0.22
            if self._incomplete_line_items(ai_result.line_items):
                reasons.append("Line item quantity, unit price, or line total is uncertain.")
                score -= 0.16
        elif ai_result.document_type == DocumentType.receipt:
            if not ai_result.extracted_amount:
                reasons.append("Receipt amount is missing.")
                score -= 0.18
            if not ai_result.extracted_date:
                reasons.append("Receipt date is missing.")
                score -= 0.10
            if not ai_result.merchant_name:
                reasons.append("Receipt merchant is missing.")
                score -= 0.08
        elif ai_result.document_type in {DocumentType.notice, DocumentType.document, DocumentType.memo, DocumentType.presentation}:
            if not ai_result.title:
                reasons.append("Document title is weak or missing.")
                score -= 0.08

        if extraction_quality.review_required:
            score -= 0.05

        score = self._clamp(score)
        review_required = score < 0.64 or bool(reasons and ai_result.review_required) or (
            ai_result.document_type in MANUFACTURING_TYPES
            and (not ai_result.line_items or self._incomplete_line_items(ai_result.line_items))
        )
        return QualityEvaluation(
            stage="post_structured_extraction",
            score=score,
            sufficient=score >= 0.58,
            review_required=review_required,
            escalation_recommended=False,
            reasons=reasons or ["Structured extraction passed the second quality gate."],
        )

    def _clamp(self, score: float) -> float:
        return max(0.0, min(0.99, round(score, 3)))

    def _is_noisy_line(self, line: str) -> bool:
        token_count = len(line.split())
        symbol_count = len(re.findall(r"[^A-Za-z0-9\s.,:/$%-]", line))
        return (token_count <= 2 and symbol_count >= 2) or bool(re.search(r"(.)\1{4,}", line))

    def _incomplete_line_items(self, line_items: list[dict] | None) -> bool:
        for item in line_items or []:
            if any(item.get(field) in (None, "", []) for field in ["quantity", "unit_price", "line_total"]):
                return True
        return False
