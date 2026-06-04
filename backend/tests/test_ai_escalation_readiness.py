from pathlib import Path
from types import SimpleNamespace
import sys

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.services.ai_escalation import should_escalate_to_ai
from app.services.file_ingestion import NormalizedDocument
from app.services.parser import DocumentParser
from app.services.quality_evaluation import DocumentQualityEvaluator


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "samples" / "pdf_ocr_readiness"


def _parsed_fixture(name: str):
    text = (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    parsed = DocumentParser().parse(text, name)
    return text, parsed


def test_text_layer_pdf_with_complete_manufacturing_data_does_not_escalate_to_ai():
    text, parsed = _parsed_fixture("text_layer_purchase_order.txt")
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_text_layer",
        normalized_text=text,
        raw_extracted_blocks=[{"type": "pdf_text", "content": text}],
        file_metadata={"table_confidence": 0.93},
    )
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)

    decision = should_escalate_to_ai(normalized, parsed, quality)

    assert decision.should_escalate is False
    assert decision.severity == "info"
    assert decision.signals["line_item_count"] == 1
    assert "missing_line_items" not in decision.reasons


def test_scanned_pdf_with_low_ocr_confidence_escalates_to_ai_even_when_some_fields_exist():
    text, parsed = _parsed_fixture("scanned_low_ocr_purchase_order.txt")
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_scanned_page_ocr",
        normalized_text=text,
        raw_extracted_blocks=[{"type": "pdf_page_ocr", "content": text}],
        ocr_confidence=0.49,
        heavy_ai_candidate=True,
        file_metadata={"table_confidence": 0.88},
    )
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)

    decision = should_escalate_to_ai(normalized, parsed, quality)

    assert decision.should_escalate is True
    assert decision.severity == "warning"
    assert decision.confidence >= 0.48
    assert "low_ocr_confidence" in decision.reasons


def test_broken_pdf_table_escalates_by_table_confidence_and_line_item_completeness():
    text, parsed = _parsed_fixture("broken_table_invoice.txt")
    normalized = NormalizedDocument(
        source_file_type="pdf",
        mime_type="application/pdf",
        extraction_method="pdf_text_layer",
        normalized_text=text,
        raw_extracted_blocks=[{"type": "pdf_table_text", "content": text}],
        file_metadata={"table_confidence": 0.42},
    )
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)

    decision = should_escalate_to_ai(normalized, parsed, quality)

    assert decision.should_escalate is True
    assert "low_table_confidence" in decision.reasons
    assert "incomplete_line_items" in decision.reasons
