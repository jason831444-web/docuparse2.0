import email
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.file_type_detection import DetectedFileType, FileTypeDetector
from app.services.office_extraction import OfficeExtractionService
from app.services.ocr import OCRService
from app.services.pdf_extraction import PdfExtractionService
from app.services.text_extraction import TextExtractionService


@dataclass
class NormalizedDocument:
    source_file_type: str
    mime_type: str
    extraction_method: str
    normalized_text: str
    raw_extracted_blocks: list[dict[str, Any]] = field(default_factory=list)
    extraction_warnings: list[str] = field(default_factory=list)
    file_metadata: dict[str, Any] = field(default_factory=dict)
    ocr_confidence: float | None = None
    primary_image_path: Path | None = None
    rendered_image_paths: list[Path] = field(default_factory=list)
    heavy_ai_candidate: bool = False
    partial_support: bool = False


class FileIngestionService:
    def __init__(
        self,
        detector: FileTypeDetector | None = None,
        ocr: OCRService | None = None,
    ) -> None:
        self.detector = detector or FileTypeDetector()
        self.ocr = ocr or OCRService()
        self.text = TextExtractionService()
        self.pdf = PdfExtractionService(self.ocr)
        self.office = OfficeExtractionService()

    def ingest(self, path: Path, original_filename: str, declared_mime: str | None = None) -> NormalizedDocument:
        detected = self.detector.detect(path, original_filename, declared_mime)
        if not detected.supported:
            return NormalizedDocument(
                source_file_type=detected.extension,
                mime_type=detected.mime_type,
                extraction_method="unsupported_file_type",
                normalized_text="",
                extraction_warnings=[detected.warning or "Unsupported file type."],
                file_metadata=self._metadata(path, detected),
                partial_support=True,
            )
        if detected.family == "image":
            return self._image(path, detected)
        if detected.family == "pdf":
            return self._pdf(path, detected)
        if detected.family in {"text", "markup", "tabular"}:
            return self._text_family(path, detected)
        if detected.family == "office":
            return self._office(path, detected)
        if detected.family == "partial":
            return self._partial(path, detected)
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method="unsupported_file_type",
            normalized_text="",
            extraction_warnings=["No ingestion strategy is available for this file type."],
            file_metadata=self._metadata(path, detected),
            partial_support=True,
        )

    def _image(self, path: Path, detected: DetectedFileType) -> NormalizedDocument:
        result = self._ocr_result(path)
        text, confidence = result["text"], result["confidence"]
        warnings = []
        if confidence < 0.68:
            warnings.append("Image OCR confidence is low or borderline; vision extraction may be needed.")
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method="image_ocr_fast_path",
            normalized_text=self._normalize_text(text),
            raw_extracted_blocks=[{"type": "image_ocr_text", "content": text, "table_blocks": result["table_blocks"]}],
            extraction_warnings=warnings,
            file_metadata={**self._metadata(path, detected), **result["metadata"]},
            ocr_confidence=confidence,
            primary_image_path=path,
            heavy_ai_candidate=True,
        )

    def _ocr_result(self, path: Path) -> dict[str, Any]:
        if hasattr(self.ocr, "extract"):
            result = self.ocr.extract(path)
            return {
                "text": result.text,
                "confidence": result.confidence,
                "table_blocks": result.table_blocks,
                "metadata": {
                    "ocr_engine": result.engine_name,
                    "ocr_confidence": result.confidence,
                    "ocr_provider_attempted": result.provider_attempted,
                    "ocr_provider_succeeded": result.provider_succeeded,
                    "ocr_provider_failed_reason": result.provider_failed_reason,
                    "ocr_table_block_count": len(result.table_blocks),
                    "ocr_line_candidate_count": len(result.line_candidates),
                    "ocr_worker_url_used": result.ocr_worker_url_used,
                    "ocr_worker_elapsed_ms": result.elapsed_ms,
                    "ocr_worker_available": result.ocr_worker_available,
                    "ocr_worker_retry_used": result.ocr_worker_metadata.get("retry_used"),
                    "ocr_worker_provider_reset_used": result.ocr_worker_metadata.get("provider_reset_used"),
                    "ocr_worker_attempt_count": result.ocr_worker_metadata.get("worker_attempt_count"),
                    "ocr_fallback_used": result.ocr_fallback_used,
                },
            }
        text, confidence = self.ocr.extract_text(path)
        return {
            "text": text,
            "confidence": confidence,
            "table_blocks": [],
            "metadata": {
                "ocr_engine": getattr(self.ocr, "engine_name", self.ocr.__class__.__name__),
                "ocr_confidence": confidence,
                "ocr_provider_attempted": [getattr(self.ocr, "engine_name", self.ocr.__class__.__name__)],
                "ocr_provider_succeeded": getattr(self.ocr, "engine_name", self.ocr.__class__.__name__),
                "ocr_provider_failed_reason": {},
                "ocr_table_block_count": 0,
                "ocr_line_candidate_count": 0,
            },
        }

    def _pdf(self, path: Path, detected: DetectedFileType) -> NormalizedDocument:
        result = self.pdf.extract(path)
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method=result.extraction_method,
            normalized_text=self._normalize_text(result.text),
            raw_extracted_blocks=result.blocks,
            extraction_warnings=result.warnings,
            file_metadata={**self._metadata(path, detected), "ocr_engine": getattr(self.ocr, "engine_name", self.ocr.__class__.__name__) if "ocr" in result.extraction_method else None, **result.metadata},
            ocr_confidence=result.ocr_confidence,
            primary_image_path=result.rendered_page_images[0] if result.rendered_page_images else None,
            rendered_image_paths=result.rendered_page_images,
            heavy_ai_candidate=result.extraction_method == "pdf_scanned_page_ocr",
            partial_support=result.extraction_method == "pdf_partial_text_extract",
        )

    def _text_family(self, path: Path, detected: DetectedFileType) -> NormalizedDocument:
        text, blocks, warnings = self.text.extract(path, detected.extension)
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method=f"{detected.extension}_direct" if detected.extension not in {"html", "htm"} else "html_text_extract",
            normalized_text=self._normalize_text(text),
            raw_extracted_blocks=blocks,
            extraction_warnings=warnings,
            file_metadata=self._metadata(path, detected),
            ocr_confidence=None,
            heavy_ai_candidate=False,
        )

    def _office(self, path: Path, detected: DetectedFileType) -> NormalizedDocument:
        text, blocks, warnings, metadata = self.office.extract(path, detected.extension)
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method=f"{detected.extension}_text_extract",
            normalized_text=self._normalize_text(text),
            raw_extracted_blocks=blocks,
            extraction_warnings=warnings,
            file_metadata={**self._metadata(path, detected), **metadata},
            partial_support=bool(warnings),
        )

    def _partial(self, path: Path, detected: DetectedFileType) -> NormalizedDocument:
        warnings = [detected.warning] if detected.warning else []
        text = ""
        blocks: list[dict[str, Any]] = []
        if detected.extension in {"rtf", "eml", "epub", "odt", "ods", "odp"}:
            text, blocks, extra_warnings = self._best_effort_partial_text(path, detected.extension)
            warnings.extend(extra_warnings)
        else:
            warnings.append(f"{detected.extension.upper()} binary extraction requires a converter such as LibreOffice or Apache Tika.")
        return NormalizedDocument(
            source_file_type=detected.extension,
            mime_type=detected.mime_type,
            extraction_method="partial_legacy_extract",
            normalized_text=self._normalize_text(text),
            raw_extracted_blocks=blocks,
            extraction_warnings=warnings or ["Partial extraction was used."],
            file_metadata=self._metadata(path, detected),
            partial_support=True,
        )

    def _best_effort_partial_text(self, path: Path, extension: str) -> tuple[str, list[dict[str, Any]], list[str]]:
        warnings = []
        try:
            data = path.read_bytes()
        except OSError as exc:
            return "", [], [f"File could not be read: {exc}."]
        if extension == "rtf":
            raw = data.decode("utf-8", errors="ignore")
            text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
            text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
            text = re.sub(r"[{}]", " ", text)
            warnings.append("RTF formatting was stripped with a best-effort parser.")
            return text, [{"type": "rtf_text", "content": text[:20000]}], warnings
        if extension == "eml":
            message = email.message_from_bytes(data)
            parts = []
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            text = "\n".join(parts)
            warnings.append("EML attachment extraction is not included in this MVP.")
            return text, [{"type": "eml_text", "content": text}], warnings
        warnings.append(f"{extension.upper()} support is partial; install a document converter for higher fidelity.")
        return self._printable_text(data), [{"type": "partial_text", "content": self._printable_text(data)[:20000]}], warnings

    def _printable_text(self, data: bytes) -> str:
        decoded = data.decode("utf-8", errors="ignore")
        return "\n".join(re.findall(r"[ -~]{8,}", decoded))

    def _normalize_text(self, text: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)

    def _metadata(self, path: Path, detected: DetectedFileType) -> dict[str, Any]:
        return {
            "source_file_type": detected.extension,
            "mime_type": detected.mime_type,
            "file_family": detected.family,
            "partial_support": detected.partial,
            "file_size_bytes": path.stat().st_size if path.exists() else None,
        }
