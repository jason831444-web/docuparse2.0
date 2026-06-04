from pathlib import Path
from types import SimpleNamespace

from app.services.item_master_matcher import ItemMasterMatcher, parse_item_master_csv
from app.services.parser import DocumentParser
from app.services.workflow_enrichment import DocumentWorkflowEnrichmentService
from app.models.document import Document


ROOT = Path(__file__).resolve().parents[2]
MANUFACTURING_ROOT = ROOT / "samples" / "manufacturing"


def _masters():
    rows, errors = parse_item_master_csv((ROOT / "samples" / "item_master_basic.csv").read_bytes())
    assert not errors
    return [SimpleNamespace(**row) for row in rows]


def _parsed_document(filename: str) -> tuple[str, object, Document]:
    text = (MANUFACTURING_ROOT / filename).read_text()
    parsed = DocumentParser().parse(text, filename)
    document = Document(
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
        line_items=parsed.line_items,
    )
    return text, parsed, document


def test_item_master_csv_parse_sample_file():
    rows, errors = parse_item_master_csv((ROOT / "samples" / "item_master_basic.csv").read_bytes())

    assert errors == []
    assert len(rows) == 10
    assert rows[0]["internal_item_code"] == "M-001"
    assert rows[0]["normalized_item_name"]
    assert rows[2]["standard_price"] == 120


def test_no_item_master_matching_keeps_codes_empty_and_adds_single_review_issue():
    text, _, document = _parsed_document("09_item_matching_ambiguous.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, [])
    document.review_required = True

    workflow = DocumentWorkflowEnrichmentService().enrich(document, text)
    messages = [issue["message_ko"] for issue in workflow.workflow_metadata["normalized_review_issues"]]

    assert all(item.get("item_master_match_status") == "skipped_no_item_master" for item in document.line_items)
    assert all(item.get("internal_item_code") in (None, "") for item in document.line_items)
    assert all("품목코드" not in str(item.get("internal_item_code") or "") for item in document.line_items)
    assert messages.count("내부 품목마스터가 없어 품목코드 매칭을 건너뛰었습니다.") == 1


def test_item_master_matching_auto_matches_known_purchase_order_items():
    _, _, document = _parsed_document("01_purchase_order_basic.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    assert document.line_items[0]["internal_item_code"] == "BOLT-M8-20"
    assert document.line_items[0]["item_master_match_status"] == "auto_matched"
    assert document.line_items[1]["internal_item_code"] == "WASH-SUS-08"
    assert document.line_items[1]["item_master_match_status"] == "auto_matched"


def test_item_master_matching_auto_matches_quotation_items():
    _, _, document = _parsed_document("03_quotation.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    assert document.line_items[0]["internal_item_code"] == "BRK-SUS-01"
    assert document.line_items[1]["internal_item_code"] == "PLT-FIX-02"


def test_ambiguous_sus_items_include_m001_candidate_without_forcing_code():
    _, _, document = _parsed_document("09_item_matching_ambiguous.txt")
    matcher = ItemMasterMatcher()
    document.line_items = matcher.match_line_items_against_masters(document.line_items, _masters())

    for item in document.line_items:
        candidate_codes = [candidate["internal_item_code"] for candidate in item.get("item_master_candidates", [])]
        assert "M-001" in candidate_codes
        assert "품목코드" not in str(item.get("internal_item_code") or "")
