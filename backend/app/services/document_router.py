import re
from dataclasses import dataclass, field
from enum import Enum

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
    DocumentType.inspection_report,
    DocumentType.contract,
}


class ProcessingPath(str, Enum):
    light = "light"
    medium = "medium"
    heavy = "heavy"


@dataclass
class DocumentRoute:
    route_label: str
    processing_path: ProcessingPath = ProcessingPath.medium
    heavy_ai_required: bool = False
    review_required: bool = False
    confidence: float = 0.75
    reasons: list[str] = field(default_factory=list)


class LightweightDocumentRouter:
    """Cheap cross-format router that decides whether lightweight extraction is enough."""

    def route(
        self,
        normalized: NormalizedDocument,
        parsed: ParsedDocument,
        quality: QualityEvaluation | None = None,
    ) -> DocumentRoute:
        text = normalized.normalized_text or ""
        stats = self._stats(text)
        reasons: list[str] = []
        quality_reasons = quality.reasons if quality else []
        quality_escalation = quality.escalation_recommended if quality else False

        if normalized.partial_support:
            return DocumentRoute(
                "partially_supported_format",
                ProcessingPath.light,
                heavy_ai_required=False,
                review_required=True,
                confidence=0.35,
                reasons=normalized.extraction_warnings or ["Format is only partially supported."],
            )

        if normalized.source_file_type in {"txt", "md", "json", "xml", "html", "htm"}:
            label = "structured_text_path" if stats["structured_density"] > 0.15 else f"{normalized.source_file_type}_direct"
            return DocumentRoute(label, ProcessingPath.light, confidence=0.86, reasons=["Direct text extraction is sufficient."])

        if normalized.source_file_type == "csv":
            return DocumentRoute("structured_text_path", ProcessingPath.light, confidence=0.88, reasons=["CSV was extracted as structured rows."])

        if normalized.source_file_type in {"docx", "xlsx", "pptx"}:
            return DocumentRoute(f"{normalized.source_file_type}_text_extract", ProcessingPath.medium, confidence=0.82, reasons=["Office text was extracted directly."])

        if normalized.extraction_method == "pdf_text_extract":
            return DocumentRoute("pdf_text_extract", ProcessingPath.medium, confidence=0.84, reasons=["PDF has a usable text layer."])

        if normalized.extraction_method == "pdf_scanned_page_ocr":
            return DocumentRoute(
                "pdf_scanned_page_ocr",
                ProcessingPath.heavy,
                heavy_ai_required=True,
                review_required=True,
                confidence=normalized.ocr_confidence or 0.55,
                reasons=["PDF appears scanned or image-based."],
            )

        if normalized.primary_image_path:
            confidence = normalized.ocr_confidence if normalized.ocr_confidence is not None else 0.55
            if parsed.document_type == DocumentType.receipt:
                return self._route_receipt_image(normalized, parsed, stats, quality, confidence)
            if quality_escalation or confidence < 0.68 or parsed.document_type == DocumentType.other or stats["line_count"] < 6:
                reasons.append("Image/layout document benefits from vision extraction.")
                return DocumentRoute("image_paddleocr_vl", ProcessingPath.heavy, True, confidence=confidence, reasons=reasons + quality_reasons[:3])
            return DocumentRoute("image_ocr_fast_path", ProcessingPath.medium, confidence=confidence, reasons=["Image OCR confidence is usable."])

        if not text.strip():
            return DocumentRoute(
                "uncertain_needs_ai",
                ProcessingPath.heavy if normalized.primary_image_path else ProcessingPath.light,
                heavy_ai_required=bool(normalized.primary_image_path),
                review_required=True,
                confidence=0.20,
                reasons=["No usable text was extracted."],
            )

        if parsed.document_type == DocumentType.receipt:
            return DocumentRoute("receipt_fast_path", ProcessingPath.medium, confidence=0.78, reasons=["Receipt-like fields were found in extracted text."])

        if parsed.document_type in MANUFACTURING_TYPES:
            review_required = not parsed.line_items or bool(quality and quality.review_required)
            return DocumentRoute(
                "manufacturing_document_fast_path",
                ProcessingPath.medium,
                confidence=0.80 if parsed.line_items else 0.62,
                review_required=review_required,
                reasons=["Manufacturing business document fields were found in extracted text."],
            )

        if parsed.document_type in {DocumentType.notice, DocumentType.document, DocumentType.memo, DocumentType.presentation}:
            return DocumentRoute("notice_document_fast_path", ProcessingPath.medium, confidence=0.80, reasons=["Document text is sufficient for lightweight parsing."])

        return DocumentRoute("structured_text_path", ProcessingPath.medium, confidence=0.70, reasons=["Generic extracted text route."])

    def _stats(self, text: str) -> dict[str, float]:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return {"line_count": 0, "structured_density": 0.0, "amount_pattern_count": 0.0, "noise_ratio": 0.0}
        structured = sum(1 for line in lines if "|" in line or ":" in line or re.search(r"\d+\.\d{2}", line))
        amount_pattern_count = len(re.findall(r"\b\d{1,6}(?:,\d{3})*\.\d{2}\b", text))
        noise_lines = sum(1 for line in lines if self._is_noisy_line(line))
        return {
            "line_count": len(lines),
            "structured_density": structured / len(lines),
            "amount_pattern_count": amount_pattern_count,
            "noise_ratio": (noise_lines / len(lines)) if lines else 0.0,
        }

    def _route_receipt_image(
        self,
        normalized: NormalizedDocument,
        parsed: ParsedDocument,
        stats: dict[str, float],
        quality: QualityEvaluation | None,
        confidence: float,
    ) -> DocumentRoute:
        receipt_complete = bool(parsed.merchant_name and parsed.extracted_date and parsed.extracted_amount)
        structured_enough = stats["amount_pattern_count"] >= 3 and stats["line_count"] >= 6
        low_noise = stats["noise_ratio"] <= 0.22
        if quality and quality.sufficient and receipt_complete and structured_enough and low_noise and confidence >= 0.72:
            return DocumentRoute(
                "receipt_image_fast_path",
                ProcessingPath.medium,
                heavy_ai_required=False,
                confidence=max(confidence, quality.score),
                reasons=["Receipt OCR already contains merchant, date, and total with usable quality."],
            )
        if quality and quality.sufficient and receipt_complete and confidence >= 0.70 and stats["noise_ratio"] <= 0.22:
            return DocumentRoute(
                "receipt_image_medium_path",
                ProcessingPath.medium,
                heavy_ai_required=False,
                confidence=max(confidence, quality.score),
                reasons=["Receipt extraction is mostly usable; heavy vision was skipped."],
            )
        return DocumentRoute(
            "image_paddleocr_vl",
            ProcessingPath.heavy,
            heavy_ai_required=True,
            review_required=True,
            confidence=confidence,
            reasons=(quality.reasons[:3] if quality else []) + ["Receipt extraction looked incomplete or ambiguous."],
        )

    def _is_noisy_line(self, line: str) -> bool:
        token_count = len(line.split())
        symbol_count = len(re.findall(r"[^A-Za-z0-9\s.,:/$%-]", line))
        return (token_count <= 2 and symbol_count >= 2) or bool(re.search(r"(.)\1{4,}", line))
