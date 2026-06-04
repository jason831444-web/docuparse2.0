import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app.models.document import Document, DocumentType, ProcessingStatus
from app.services.export import document_to_json, documents_to_csv
from app.services.item_master_matcher import ItemMasterMatcher, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService

sys.modules.setdefault(
    "pytesseract",
    SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=lambda image: "",
        image_to_data=lambda image, output_type=None: {"conf": []},
    ),
)


ROOT = Path(__file__).resolve().parents[2]
COMPLEX_ROOT = ROOT / "samples" / "complex_manufacturing"


def _text(name: str) -> str:
    return (COMPLEX_ROOT / name).read_text()


def _masters():
    rows, errors = parse_item_master_csv((COMPLEX_ROOT / "item_master_complex_docuparse_ready.csv").read_bytes())
    assert not errors
    return [SimpleNamespace(**row) for row in rows]


def _processed(name: str):
    text = _text(name)
    parsed = DocumentParser().parse(text, name)
    line_items = ItemMasterMatcher().match_line_items_against_masters(parsed.line_items, _masters())
    document = Document(
        original_filename=name,
        stored_file_path=f"/tmp/{name}",
        mime_type="text/plain",
        document_type=parsed.document_type,
        vendor_name=parsed.vendor_name,
        customer_name=parsed.customer_name,
        document_number=parsed.document_number,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        extracted_amount=parsed.extracted_amount,
        currency=parsed.currency,
        line_items=line_items,
    )
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    return parsed, document, workflow


def _issue_codes(workflow):
    return [issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]]


class FakeSession:
    def __init__(self, document: Document) -> None:
        self.document = document

    def add(self, document: Document) -> None:
        self.document = document

    def commit(self) -> None:
        return None

    def refresh(self, document: Document) -> None:
        return None

    def rollback(self) -> None:
        return None

    def get(self, model, document_id):
        return self.document


def test_complex_invoice_profile_and_workflow_are_consistently_invoice():
    parsed, document, workflow = _processed("02_complex_invoice_tax_due_and_partial_codes.txt")

    assert parsed.document_type == DocumentType.invoice
    assert parsed.category == "invoice"
    assert document.document_number == "INV-2026-0702-332"
    assert document.vendor_name == "성진전자부품"
    assert document.customer_name == "네오팩토리"
    assert document.issue_date.isoformat() == "2026-07-02"
    assert document.due_date.isoformat() == "2026-08-01"
    assert workflow.workflow_metadata["workflow_mode"] == "invoice"
    assert workflow.workflow_metadata["content_profile"] == "invoice"
    assert workflow.workflow_metadata["review_required"] is False
    assert "견적" not in (workflow.workflow_summary or "")
    assert "missing_document_item_code" in _issue_codes(workflow)
    assert workflow.warnings == []


def test_complex_invoice_processing_normalizes_stale_interpretation_profile(tmp_path):
    from app.services.category_interpretation import CategoryInterpretation
    from app.services.document_processor import DocumentProcessor

    source = tmp_path / "complex_invoice.txt"
    source.write_text(_text("02_complex_invoice_tax_due_and_partial_codes.txt"), encoding="utf-8")
    document = Document(
        original_filename=source.name,
        stored_file_path=str(source),
        mime_type="text/plain",
        processing_status=ProcessingStatus.uploaded,
    )
    processor = DocumentProcessor()

    class StaleInterpreter:
        def interpret(self, document, text):
            return CategoryInterpretation(
                category="quotation",
                profile="quotation",
                subtype="quotation",
                summary_hint="잘못된 견적서 해석",
                provider="heuristic_interpretation",
                provider_chain=["heuristic_interpretation"],
            )

    processor.heuristic_interpreter = StaleInterpreter()
    result = processor.process(FakeSession(document), document)
    category_interpretation = result.workflow_metadata["category_interpretation"]

    assert result.document_type == DocumentType.invoice
    assert result.category == "invoice"
    assert result.ai_document_type == DocumentType.invoice
    assert result.workflow_metadata["workflow_mode"] == "invoice"
    assert result.workflow_metadata["content_profile"] == "invoice"
    assert category_interpretation["profile"] == "invoice"
    assert category_interpretation["category"] == "invoice"
    assert "견적" not in (result.workflow_summary or "")


def test_complex_delivery_note_without_price_columns_is_ready():
    parsed, document, workflow = _processed("03_complex_delivery_note_no_prices.txt")

    assert parsed.document_type == DocumentType.delivery_note
    assert len(document.line_items) == 4
    assert workflow.workflow_metadata["review_required"] is False
    assert "missing_price_or_total" not in _issue_codes(workflow)
    assert workflow.warnings == []
    assert all(item.get("quantity") for item in document.line_items)
    assert all(item.get("unit") == "EA" for item in document.line_items)


def test_complex_delivery_note_processing_suppresses_generic_review_fallback(tmp_path):
    from app.services.document_processor import DocumentProcessor

    source = tmp_path / "complex_delivery_note.txt"
    source.write_text(_text("03_complex_delivery_note_no_prices.txt"), encoding="utf-8")
    document = Document(
        original_filename=source.name,
        stored_file_path=str(source),
        mime_type="text/plain",
        processing_status=ProcessingStatus.uploaded,
    )
    result = DocumentProcessor().process(FakeSession(document), document)
    issues = result.workflow_metadata["normalized_review_issues"]

    assert result.document_type == DocumentType.delivery_note
    assert len(result.line_items) == 4
    assert result.review_required is False
    assert result.processing_status == ProcessingStatus.ready
    assert result.warnings == []
    assert issues == []
    assert "검토 필요 항목을 확인하세요" not in json.dumps(result.workflow_metadata, ensure_ascii=False)


def test_complex_missing_quantity_keeps_warning_text_out_of_numeric_field():
    _, document, workflow = _processed("06_complex_quote_missing_quantity_but_amount_present.txt")
    document.review_required = workflow.workflow_metadata["review_required"]

    assert workflow.workflow_metadata["review_required"] is True
    assert _issue_codes(workflow).count("missing_quantity") == 1
    assert document.line_items[0].get("quantity") in (None, "")
    assert "비어 있습니다" not in str(document.line_items[0].get("quantity") or "")
    assert document.line_items[0]["unit"] == "EA"
    assert document.line_items[0]["unit_price"] == 2800
    assert document.line_items[0]["line_total"] == 308000

    exported_json = document_to_json(document)
    exported_csv = documents_to_csv([document])
    exported_payload = json.loads(exported_json)
    assert "의 수량이 비어 있습니다" not in exported_json
    assert "의 수량이 비어 있습니다" not in exported_csv
    assert exported_payload["line_items"][0].get("quantity") in (None, "")
    for item in exported_payload["line_items"]:
        for field in ["quantity", "unit_price", "supply_amount", "tax_amount", "line_total", "item_code", "internal_item_code"]:
            assert "비어 있습니다" not in str(item.get(field) or "")
            assert "미확인" not in str(item.get(field) or "")


def test_complex_transaction_statement_uses_transaction_date_as_primary_date():
    parsed, _, workflow = _processed("08_complex_transaction_statement_with_duplicate_lines.txt")

    assert parsed.document_type == DocumentType.transaction_statement
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert workflow.workflow_metadata["review_required"] is False
    assert "missing_issue_date" not in _issue_codes(workflow)
    assert workflow.warnings == []


def test_complex_invoice_table_rows_are_not_reparsed_as_duplicate_sku_items():
    parsed, document, workflow = _processed("09_complex_invoice_unknown_item_and_foreign_currency.txt")
    document.review_required = workflow.workflow_metadata["review_required"]

    assert parsed.document_type == DocumentType.invoice
    assert document.currency == "USD"
    assert document.extracted_amount == parsed.extracted_amount
    assert str(document.extracted_amount) == "508.00"
    assert len(document.line_items) == 3
    assert [item["item_name"] for item in document.line_items] == [
        "Linear Guide Rail HGW20",
        "Cable Harness 500",
        "PCB Connector 12P",
    ]
    assert [item["item_code"] for item in document.line_items] == ["HGW20-1000", "CBL-HAR-500", "CON-PCB-12P"]
    assert all(item.get("item_name") not in {"HGW20-1000", "CBL-HAR-500", "CON-PCB-12P"} for item in document.line_items)
    assert "amount_mismatch" not in _issue_codes(workflow)
    assert _issue_codes(workflow).count("internal_item_unmatched") == 1

    exported_json = document_to_json(document)
    exported_csv = documents_to_csv([document])
    exported_payload = json.loads(exported_json)
    assert len(exported_payload["line_items"]) == 3
    assert exported_csv.count("Linear Guide Rail HGW20") == 1
    assert exported_csv.count("Cable Harness 500") == 1
    assert exported_csv.count("PCB Connector 12P") == 1


def test_complex_amount_mismatch_uses_document_level_total_not_note_text():
    _, document, workflow = _processed("05_complex_po_amount_mismatch_and_rounding.txt")

    assert document.extracted_amount == 500000
    assert workflow.workflow_metadata["review_required"] is True
    assert _issue_codes(workflow).count("amount_mismatch") == 1
    amount_issue = next(issue for issue in workflow.workflow_metadata["normalized_review_issues"] if issue["code"] == "amount_mismatch")
    assert amount_issue["expected"] == "495000"
    assert amount_issue["actual"] == "500000"
    assert amount_issue["document_total"] == "500000"
    assert amount_issue["line_total_sum"] == "495000"
    assert amount_issue["difference"] == "5000"
    assert amount_issue["currency"] == "KRW"
    assert "500,000원" in amount_issue["message_ko"]
    assert "495,000원" in amount_issue["message_ko"]
    assert "5,000원" in amount_issue["message_ko"]


def test_complex_ambiguous_matching_reports_line_level_invalid_amount():
    _, _, workflow = _processed("07_complex_item_master_matching_ambiguous_aliases.txt")
    issues = workflow.workflow_metadata["normalized_review_issues"]
    invalid_issues = [issue for issue in issues if issue["code"] == "invalid_line_amount"]

    assert "amount_mismatch" in _issue_codes(workflow)
    assert invalid_issues
    assert any(issue.get("item_index") == 3 for issue in invalid_issues)


def test_complex_broad_stainless_material_requires_candidate_review_but_explicit_grades_match():
    _, document, workflow = _processed("04_complex_quote_with_ambiguous_material.txt")

    assert document.line_items[0]["item_name"] == "스텐판 2T"
    assert document.line_items[0].get("internal_item_code") in (None, "")
    assert document.line_items[0]["item_master_match_status"] == "ambiguous"
    assert any(candidate["internal_item_code"] == "M-PLT-SUS304-2T-1000X2000" for candidate in document.line_items[0]["item_master_candidates"])
    assert any(candidate["internal_item_code"] == "M-PLT-SUS316-2T-1000X2000" for candidate in document.line_items[0]["item_master_candidates"])
    assert document.line_items[1]["item_name"] == "SUS304 철판 3T"
    assert document.line_items[1]["internal_item_code"] == "M-PLT-SUS304-3T-1000X2000"
    assert document.line_items[1]["item_master_match_status"] in {"alias_matched", "auto_matched"}
    assert workflow.workflow_metadata["review_required"] is True
    assert "internal_item_ambiguous" in _issue_codes(workflow)
