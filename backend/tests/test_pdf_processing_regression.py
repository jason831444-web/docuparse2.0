import sys
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)

from app.models.document import Document, DocumentType
from app.services.ai_escalation import should_escalate_to_ai
from app.services.file_ingestion import NormalizedDocument
from app.services.item_master_matcher import ItemMasterMatcher, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.pdf_extraction import PdfExtractionService
from app.services.quality_evaluation import DocumentQualityEvaluator
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


ROOT = Path(__file__).resolve().parents[2]
COMPLEX_ROOT = ROOT / "samples" / "complex_manufacturing"


def _masters():
    rows, errors = parse_item_master_csv((COMPLEX_ROOT / "item_master_complex_docuparse_ready.csv").read_bytes())
    assert not errors
    return [SimpleNamespace(**row) for row in rows if row["active"]]


def _pdf_escape(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped})"


def _write_text_pdf(path: Path, lines: list[str]) -> None:
    ops = ["BT", "/F1 9 Tf", "40 780 Td"]
    for index, line in enumerate(lines):
        if index:
            ops.append("0 -13 Td")
        ops.append(f"{_pdf_escape(line)} Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        output += f"{offset:010d} 00000 n \n".encode()
    output += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(output)


def _document(parsed, filename: str, text: str, line_items=None) -> Document:
    return Document(
        original_filename=filename,
        stored_file_path=f"/tmp/{filename}",
        mime_type="application/pdf",
        document_type=parsed.document_type,
        vendor_name=parsed.vendor_name,
        customer_name=parsed.customer_name,
        document_number=parsed.document_number,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        extracted_amount=parsed.extracted_amount,
        subtotal=parsed.subtotal,
        tax=parsed.tax,
        currency=parsed.currency,
        line_items=line_items if line_items is not None else parsed.line_items,
    )


def test_text_layer_pdf_quotation_extracts_line_items_and_keeps_ambiguous_material_for_review(tmp_path):
    pdf_path = tmp_path / "quotation_text_layer.pdf"
    _write_text_pdf(pdf_path, [
        "Quotation",
        "Quotation No: QT-PDF-1001",
        "Quotation Date: 2026-07-10",
        "Valid Until: 2026-07-25",
        "Supplier: Test Metals",
        "Customer: Future Factory",
        "Delivery Terms: 10 days after order",
        "Payment Terms: Net 30",
        "Item Name | Item Code | Spec | Qty | Unit | Unit Price | Supply Amount | VAT | Line Total",
        "Stainless Plate 2T | | 2.0T 1000x2000 | 5 | EA | 25000 | 125000 | 12500 | 137500",
        "SUS304 Plate 3T | | 3.0T 1000x2000 | 2 | EA | 37000 | 74000 | 7400 | 81400",
        "Fix Bracket | BRK-FIX-01 | 40x60x3T | 10 | EA | 1000 | 10000 | 1000 | 11000",
        "Supply Amount Total: 209000",
        "VAT Total: 20900",
        "Grand Total: 229900",
    ])

    extracted = PdfExtractionService().extract(pdf_path)
    parsed = DocumentParser().parse(extracted.text, pdf_path.name)
    matched = ItemMasterMatcher().match_line_items_against_masters(parsed.line_items, _masters())
    document = _document(parsed, pdf_path.name, extracted.text, matched)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, extracted.text)
    normalized = NormalizedDocument("pdf", "application/pdf", extracted.extraction_method, extracted.text, file_metadata={"table_confidence": 0.92})
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)
    escalation = should_escalate_to_ai(normalized, parsed, quality)

    assert parsed.document_type == DocumentType.quotation
    assert parsed.category == "quotation"
    assert len(parsed.line_items) == 3
    assert parsed.extracted_amount == 229900
    assert matched[0]["item_master_match_status"] == "ambiguous"
    assert matched[0].get("internal_item_code") in (None, "")
    assert workflow.workflow_metadata["content_profile"] == "quotation"
    assert escalation.should_escalate is False


def test_text_layer_pdf_invoice_keeps_vendor_sku_as_document_code_without_duplicate_item(tmp_path):
    pdf_path = tmp_path / "invoice_text_layer.pdf"
    _write_text_pdf(pdf_path, [
        "Tax Invoice",
        "Invoice No: INV-PDF-2001",
        "Issue Date: 2026-07-11",
        "Payment Due: 2026-07-31",
        "Supplier: Seongjin Electronics",
        "Bill To: Neo Factory",
        "No | Item Name | Vendor SKU | Spec | Qty | Unit | Unit Price | Supply Amount | VAT | Line Total",
        "1 | PCB Connector 12P | CON-PCB-12P | 12pin | 1500 | EA | 300 | 450000 | 45000 | 495000",
        "2 | Cable Harness 500 | CBL-HAR-500 | 500mm | 20 | EA | 2200 | 44000 | 4400 | 48400",
        "Supply Amount Total: 494000",
        "VAT Total: 49400",
        "Invoice Total: 543400",
    ])

    extracted = PdfExtractionService().extract(pdf_path)
    parsed = DocumentParser().parse(extracted.text, pdf_path.name)
    item_names = [item.get("item_name") for item in parsed.line_items]

    assert parsed.document_type == DocumentType.invoice
    assert parsed.document_number == "INV-PDF-2001"
    assert parsed.due_date.isoformat() == "2026-07-31"
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["item_code"] == "CON-PCB-12P"
    assert "CON-PCB-12P" not in item_names
    assert parsed.extracted_amount == 543400


def test_scanned_pdf_ocr_delivery_note_no_price_can_be_ready_when_ocr_is_good(monkeypatch, tmp_path):
    text = "\n".join([
        "Delivery Note",
        "Delivery Note No: DN-PDF-3001",
        "Issue Date: 2026-07-12",
        "Delivery Date: 2026-07-13",
        "Supplier: Parts Hub",
        "Customer: Future Factory",
        "Item Name / Vendor SKU / Spec / Delivery Qty / Unit",
        "Bearing Housing / BRG-H-100 / 100mm / 25 / EA",
    ])
    pdf_path = tmp_path / "delivery_scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% image-only placeholder\n")

    monkeypatch.setattr(PdfExtractionService, "_extract_text", lambda self, path: ("", 1, []))
    monkeypatch.setattr(PdfExtractionService, "_render_pages", lambda self, path, max_pages: [tmp_path / "page-1.png"])

    class FakeOCR:
        def extract_text(self, path):
            return text, 0.91

    extracted = PdfExtractionService(ocr=FakeOCR()).extract(pdf_path)
    parsed = DocumentParser().parse(extracted.text, pdf_path.name)
    document = _document(parsed, pdf_path.name, extracted.text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, extracted.text)
    normalized = NormalizedDocument("pdf", "application/pdf", extracted.extraction_method, extracted.text, ocr_confidence=extracted.ocr_confidence, file_metadata={"table_confidence": 0.86})
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)
    escalation = should_escalate_to_ai(normalized, parsed, quality)

    assert extracted.extraction_method == "pdf_scanned_page_ocr"
    assert parsed.document_type == DocumentType.delivery_note
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["quantity"] == 25
    assert workflow.workflow_metadata["review_required"] is False
    assert "검토 필요 항목을 확인하세요." not in workflow.warnings
    assert escalation.should_escalate is False


def test_scanned_pdf_with_poor_ocr_and_broken_table_escalates_to_ai(monkeypatch, tmp_path):
    text = "\n".join([
        "Tax Invoice",
        "Invoice No: INV-BROKEN-PDF",
        "Supplier: Parts Hub",
        "No | Item Name | Vendor SKU | Spec | Qty | Unit | Unit Price | Supply Amount | VAT | Line Total",
        "1 | PCB Connector 12P | CON-PCB-12P | 12pin | | EA | 300 | 450000 | 45000 | 495000",
    ])
    pdf_path = tmp_path / "broken_scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% image-only placeholder\n")
    monkeypatch.setattr(PdfExtractionService, "_extract_text", lambda self, path: ("", 1, []))
    monkeypatch.setattr(PdfExtractionService, "_render_pages", lambda self, path, max_pages: [tmp_path / "page-1.png"])

    class FakeOCR:
        def extract_text(self, path):
            return text, 0.52

    extracted = PdfExtractionService(ocr=FakeOCR()).extract(pdf_path)
    parsed = DocumentParser().parse(extracted.text, pdf_path.name)
    normalized = NormalizedDocument("pdf", "application/pdf", extracted.extraction_method, extracted.text, ocr_confidence=extracted.ocr_confidence, heavy_ai_candidate=True, file_metadata={"table_confidence": 0.41})
    quality = DocumentQualityEvaluator().evaluate_extraction(normalized, parsed)
    escalation = should_escalate_to_ai(normalized, parsed, quality)

    assert escalation.should_escalate is True
    assert escalation.severity == "warning"
    assert escalation.confidence > 0
    assert {"low_ocr_confidence", "low_table_confidence", "incomplete_line_items"} & set(escalation.reasons)


def test_malformed_amount_pdf_creates_line_amount_warning_and_amount_mismatch(tmp_path):
    pdf_path = tmp_path / "malformed_amount.pdf"
    _write_text_pdf(pdf_path, [
        "Purchase Order",
        "PO No: PO-PDF-5001",
        "Issue Date: 2026-07-14",
        "Due Delivery: 2026-07-21",
        "Supplier: Test Metals",
        "Customer: Future Factory",
        "Item Name | Item Code | Spec | Qty | Unit | Unit Price | Supply Amount | VAT | Line Total",
        "SUS316 PLATE 2T | | 1000x2000 | 1 | EA | 42000 | 4200 | 46200 | 4200",
        "Grand Total: 46200",
    ])

    extracted = PdfExtractionService().extract(pdf_path)
    parsed = DocumentParser().parse(extracted.text, pdf_path.name)
    document = _document(parsed, pdf_path.name, extracted.text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, extracted.text)
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert any(issue["code"] == "invalid_line_amount" for issue in issues)
    assert any(issue["code"] == "amount_mismatch" for issue in issues)
    assert workflow.workflow_metadata["review_required"] is True
