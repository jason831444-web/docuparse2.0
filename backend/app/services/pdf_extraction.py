from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.ocr import OCRService


@dataclass
class PdfExtractionResult:
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    rendered_page_images: list[Path] = field(default_factory=list)
    ocr_confidence: float | None = None
    extraction_method: str = "pdf_text_extract"


class PdfExtractionService:
    def __init__(self, ocr: OCRService | None = None) -> None:
        self.ocr = ocr or OCRService()
        self.settings = get_settings()

    def extract(self, path: Path) -> PdfExtractionResult:
        result = PdfExtractionResult()
        text, page_count, warnings = self._extract_text(path)
        result.warnings.extend(warnings)
        result.metadata["page_count"] = page_count
        result.metadata["file_size_bytes"] = path.stat().st_size if path.exists() else None

        if len(text.strip()) >= 80:
            result.text = text
            result.blocks = [{"type": "pdf_text", "content": text[:20000]}]
            result.metadata["text_layer_exists"] = True
            result.metadata["image_only"] = False
            result.metadata["ocr_engine"] = None
            return result

        result.warnings.append("PDF text layer was sparse; OCR was attempted on rendered pages.")
        result.metadata["text_layer_exists"] = bool(text.strip())
        result.metadata["image_only"] = not bool(text.strip())
        rendered = self._render_pages(path, max_pages=self.settings.pdf_ocr_max_pages)
        result.rendered_page_images = rendered
        if not rendered:
            result.text = text
            result.extraction_method = "pdf_partial_text_extract"
            result.warnings.append("PDF pages could not be rendered for OCR in this environment.")
            return result

        ocr_texts = []
        page_blocks: list[dict[str, Any]] = []
        confidences = []
        provider_attempted: list[str] = []
        provider_failed_reason: dict[str, str] = {}
        provider_succeeded: str | None = None
        table_block_count = 0
        line_candidate_count = 0
        worker_elapsed_ms = 0
        worker_url_used: str | None = None
        worker_available: bool | None = None
        fallback_used = False
        worker_retry_used = False
        worker_provider_reset_used = False
        worker_attempt_count = 0
        for index, image_path in enumerate(rendered, start=1):
            if hasattr(self.ocr, "extract"):
                ocr_result = self.ocr.extract(image_path)
                page_text, confidence = ocr_result.text, ocr_result.confidence
                provider_attempted.extend(ocr_result.provider_attempted)
                provider_failed_reason.update(ocr_result.provider_failed_reason)
                provider_succeeded = provider_succeeded or ocr_result.provider_succeeded
                table_block_count += len(ocr_result.table_blocks)
                page_line_candidates = [_with_page(candidate, index) for candidate in ocr_result.line_candidates]
                line_candidate_count += len(page_line_candidates)
                worker_elapsed_ms += ocr_result.elapsed_ms or 0
                worker_url_used = worker_url_used or ocr_result.ocr_worker_url_used
                worker_available = ocr_result.ocr_worker_available if worker_available is None else worker_available
                fallback_used = fallback_used or ocr_result.ocr_fallback_used
                worker_retry_used = worker_retry_used or bool(ocr_result.ocr_worker_metadata.get("retry_used"))
                worker_provider_reset_used = worker_provider_reset_used or bool(ocr_result.ocr_worker_metadata.get("provider_reset_used"))
                worker_attempt_count += int(ocr_result.ocr_worker_metadata.get("worker_attempt_count") or 0)
            else:
                page_text, confidence = self.ocr.extract_text(image_path)
                provider_name = getattr(self.ocr, "engine_name", self.ocr.__class__.__name__)
                provider_attempted.append(provider_name)
                provider_succeeded = provider_succeeded or provider_name
                page_line_candidates = []
                ocr_result = None
            confidences.append(confidence)
            if page_text.strip():
                ocr_texts.append(f"Page {index}\n{page_text.strip()}")
            page_blocks.append({
                "type": "pdf_page_ocr",
                "page": index,
                "image_path": str(image_path),
                "content": f"Page {index}\n{page_text.strip()}" if page_text.strip() else "",
                "table_blocks": ocr_result.table_blocks if ocr_result is not None else [],
                "line_candidates": page_line_candidates,
            })
        result.text = "\n\n".join(ocr_texts).strip()
        result.blocks = page_blocks
        result.ocr_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        result.extraction_method = "pdf_scanned_page_ocr"
        result.metadata["ocr_engine"] = provider_succeeded or getattr(self.ocr, "engine_name", self.ocr.__class__.__name__)
        result.metadata["ocr_page_count"] = len(rendered)
        result.metadata["ocr_provider_attempted"] = list(dict.fromkeys(provider_attempted))
        result.metadata["ocr_provider_succeeded"] = provider_succeeded
        result.metadata["ocr_provider_failed_reason"] = provider_failed_reason
        result.metadata["ocr_table_block_count"] = table_block_count
        result.metadata["ocr_line_candidate_count"] = line_candidate_count
        result.metadata["ocr_worker_url_used"] = worker_url_used
        result.metadata["ocr_worker_elapsed_ms"] = worker_elapsed_ms or None
        result.metadata["ocr_worker_available"] = worker_available
        result.metadata["ocr_worker_retry_used"] = worker_retry_used
        result.metadata["ocr_worker_provider_reset_used"] = worker_provider_reset_used
        result.metadata["ocr_worker_attempt_count"] = worker_attempt_count or None
        result.metadata["ocr_fallback_used"] = fallback_used
        if page_count and len(rendered) < page_count:
            result.warnings.append(f"OCR was limited to the first {len(rendered)} of {page_count} PDF pages.")
        if not result.text:
            result.warnings.append("No readable text was extracted from the scanned PDF.")
        return result

    def _extract_text(self, path: Path) -> tuple[str, int | None, list[str]]:
        warnings: list[str] = []
        try:
            from pypdf import PdfReader
        except Exception as exc:
            return "", None, [f"PDF text extraction dependency is unavailable: {exc}."]

        try:
            reader = PdfReader(str(path))
            page_texts = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(f"Page {index}\n{text.strip()}")
            return "\n\n".join(page_texts), len(reader.pages), warnings
        except Exception as exc:
            return "", None, [f"PDF text extraction failed: {exc}."]

    def _render_pages(self, path: Path, max_pages: int) -> list[Path]:
        try:
            import fitz
        except Exception:
            return []

        output_dir = self.settings.upload_dir / "rendered_pages"
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        try:
            document = fitz.open(path)
            for page_index in range(min(len(document), max_pages)):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                output_path = output_dir / f"{path.stem}-page-{page_index + 1}.png"
                pixmap.save(output_path)
                rendered.append(output_path)
            document.close()
        except Exception:
            return rendered
        return rendered


def _with_page(candidate: dict[str, Any], page: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {"text": str(candidate), "page": page}
    if candidate.get("page") == page:
        return candidate
    return {**candidate, "page": page}
