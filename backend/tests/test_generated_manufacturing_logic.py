from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace

from app.models.document import Document, DocumentType
from app.services.item_master_matcher import ItemMasterMatcher, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "samples" / "generated_logic_tests"


def _text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text()


def _masters(active_only: bool = True):
    rows, errors = parse_item_master_csv((FIXTURE_ROOT / "item_master_logic.csv").read_bytes())
    assert not errors
    if active_only:
        rows = [row for row in rows if row["active"]]
    return [SimpleNamespace(**row) for row in rows]


def _document(parsed, filename: str, text: str, *, match: bool = False) -> Document:
    line_items = parsed.line_items
    if match:
        line_items = ItemMasterMatcher().match_line_items_against_masters(line_items, _masters())
    return Document(
        original_filename=filename,
        stored_file_path=f"/tmp/{filename}",
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


def test_due_request_date_and_comma_table_document_item_code_are_extracted():
    text = _text("purchase_order_due_request.txt")
    parsed = DocumentParser().parse(text, "generated_po.txt")
    item = parsed.line_items[0]

    assert parsed.document_type == DocumentType.purchase_order
    assert parsed.document_number == "PO-GEN-1001"
    assert parsed.issue_date.isoformat() == "2026-07-20"
    assert parsed.due_date.isoformat() == "2026-07-28"
    assert parsed.vendor_name == "대한테스트부품"
    assert parsed.customer_name == "한빛테스트제조"
    assert item["item_code"] == "CON-PCB-12P"
    assert item["document_item_code"] == "CON-PCB-12P"
    assert item["source_item_code"] == "CON-PCB-12P"
    assert item["quantity"] == 1500
    assert item["supply_amount"] == 450000
    assert item["tax_amount"] == 45000
    assert item["line_total"] == 495000


def test_tax_invoice_classification_customer_and_workflow_labels_are_invoice_specific():
    text = _text("tax_invoice_customer.txt")
    parsed = DocumentParser().parse(text, "generated_invoice.txt")
    document = _document(parsed, "generated_invoice.txt", text, match=True)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)

    assert parsed.document_type == DocumentType.invoice
    assert parsed.category == "invoice"
    assert parsed.document_number == "INV-GEN-2001"
    assert parsed.vendor_name == "성진테스트전자"
    assert parsed.customer_name == "네오팩토리"
    assert parsed.issue_date.isoformat() == "2026-07-21"
    assert parsed.due_date.isoformat() == "2026-08-05"
    assert parsed.line_items[0]["item_code"] == "CBL-HAR-500"
    assert document.line_items[0]["item_master_match_status"] == "alias_matched"
    assert document.line_items[0]["internal_item_code"] == "INT-CBL-500"
    assert "견적서" not in (workflow.workflow_summary or "")
    assert "견적번호" not in (workflow.workflow_summary or "")
    assert any(date_label.startswith("지급기한") for date_label in workflow.key_dates)
    assert workflow.workflow_metadata["workflow_mode"] == "invoice"


def test_slash_separated_delivery_note_items_without_prices_are_valid():
    text = _text("delivery_note_slash_no_price.txt")
    parsed = DocumentParser().parse(text, "generated_delivery_note.txt")
    document = _document(parsed, "generated_delivery_note.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issue_codes = [issue["code"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert parsed.document_type == DocumentType.delivery_note
    assert len(parsed.line_items) == 2
    assert parsed.line_items[0]["item_code"] == "BRG-H-100"
    assert parsed.line_items[0]["quantity"] == 25
    assert parsed.line_items[0]["unit"] == "EA"
    assert "missing_price_or_total" not in issue_codes
    assert "missing_line_items" not in issue_codes


def test_malformed_amounts_create_review_issue_without_corrupting_numeric_fields():
    text = _text("malformed_amounts.txt")
    parsed = DocumentParser().parse(text, "generated_malformed_amounts.txt")
    document = _document(parsed, "generated_malformed_amounts.txt", text)
    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert parsed.line_items[0]["supply_amount"] == 3000
    assert parsed.line_items[0]["tax_amount"] == 5000
    assert parsed.line_items[0]["line_total"] == 4000
    assert all("미확인" not in str(parsed.line_items[0].get(field, "")) for field in ["item_code", "quantity", "tax_amount", "line_total"])
    assert any(issue["code"] == "invalid_line_amount" for issue in issues)
    assert workflow.workflow_metadata["review_required"] is True


def test_item_code_name_conflict_blocks_auto_ready():
    document = Document(
        original_filename="conflict_invoice.txt",
        stored_file_path="/tmp/conflict_invoice.txt",
        mime_type="text/plain",
        document_type=DocumentType.invoice,
        vendor_name="동진부품",
        customer_name="오성테크",
        document_number="INV-1",
        extracted_amount=Decimal("132000"),
        currency="KRW",
        line_items=[
            {
                "item_name": "S45C PIN 8X60",
                "internal_item_code": "P-PIN-S45C-08X60",
                "quantity": 200,
                "unit": "EA",
                "unit_price": 600,
                "line_total": 132000,
                "validation_warnings": ["item_code_name_conflict"],
            }
        ],
    )
    workflow = DocumentWorkflowEnrichmentService().enrich(document, "세금계산서")
    issues = workflow.workflow_metadata["normalized_review_issues"]

    assert any(issue["code"] == "item_code_name_conflict" for issue in issues)
    assert workflow.workflow_metadata["review_required"] is True


def test_alias_matching_ambiguous_duplicate_and_inactive_master_policy():
    matcher = ItemMasterMatcher()

    direct = matcher.match_line_items_against_masters(
        [{"item_name": "Some name", "item_code": "INT-PCB-12P", "quantity": 1, "unit": "EA", "unit_price": 300, "line_total": 300}],
        _masters(active_only=False),
    )[0]
    alias = matcher.match_line_items_against_masters(
        [{"item_name": "Cable Harness 500mm", "item_code": "CBL-HAR-500", "quantity": 1, "unit": "EA", "unit_price": 2200, "line_total": 2200}],
        _masters(active_only=False),
    )[0]
    ambiguous = matcher.match_line_items_against_masters(
        [{"item_name": "SUS-304 판재", "specification": "1000x2000", "quantity": 1, "unit": "EA", "unit_price": 25000, "line_total": 25000}],
        _masters(active_only=False),
    )[0]

    assert direct["item_master_match_status"] == "direct_code_match"
    assert direct["internal_item_code"] == "INT-PCB-12P"
    assert alias["item_master_match_status"] == "alias_matched"
    assert alias["internal_item_code"] == "INT-CBL-500"
    assert ambiguous["item_master_match_status"] == "ambiguous"
    assert ambiguous.get("internal_item_code") in (None, "")
    candidate_codes = {candidate["internal_item_code"] for candidate in ambiguous["item_master_candidates"]}
    assert "INT-INACTIVE-PLATE" not in candidate_codes


def test_missing_document_item_code_is_info_when_internal_match_is_confident_and_issues_are_deduped():
    text = _text("ambiguous_matching.txt")
    parsed = DocumentParser().parse(text, "generated_ambiguous.txt")
    item = parsed.line_items[0]
    item["internal_item_code"] = "INT-SUS304-2T-A"
    item["item_master_match_status"] = "auto_matched"
    document = _document(parsed, "generated_ambiguous.txt", text)
    document.line_items = [item]
    document.low_confidence_fields = ["missing_document_item_code:item_1", "missing_document_item_code:item_1"]

    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    issues = workflow.workflow_metadata["normalized_review_issues"]
    missing_code_issues = [issue for issue in issues if issue["code"] == "missing_document_item_code"]

    assert len(missing_code_issues) == 1
    assert missing_code_issues[0]["severity"] == "info"
    assert workflow.workflow_metadata["review_required"] is False
    assert not any(issue["code"] in {"internal_item_unmatched", "internal_item_ambiguous"} for issue in issues)
